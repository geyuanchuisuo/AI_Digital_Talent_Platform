"""T-10 L2：批量上传集成测试（真实 MySQL + 真实磁盘，不外呼 LLM）。

覆盖 todolist T-10 三个验收点：
1. PDF/DOCX 混合上传成功且返回 `upload_batch_id`（并落 `resume_files` + `parse_tasks(pending)`）
2. 同一文件重传进 `duplicates`，**改文件名重传仍进 duplicates**（`resume_files` 永不物理删除）
3. 伪造扩展名（exe 内容改 .pdf）被 magic bytes 拦截

隔离方式：每轮用带随机 tag 的合成文件（内容不同 → 哈希不同），批号登记到 `cleanup`
fixture，用例结束删行 + 删文件，不在库里留垃圾。
"""

from __future__ import annotations

import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal
from app.models import ParseTask, ResumeFile
from app.service.upload_service import resolve_stored_path

pytestmark = pytest.mark.integration


def make_pdf(tag: str) -> bytes:
    """带唯一注释的最小 PDF：内容随 tag 变化 → SHA-256 不同，避免跨用例撞哈希。"""
    return (
        b"%PDF-1.4\n"
        + f"% {tag}\n".encode()
        + b"1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    )


def make_docx(tag: str) -> bytes:
    import docx

    document = docx.Document()
    document.add_paragraph(f"姓名：{tag}")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_fake_pdf() -> bytes:
    """exe（PE 头）伪装成 .pdf。"""
    return b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xffThis is not a pdf" + uuid4().hex.encode()


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/login", json={"username": "hr", "password": "hr123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


@pytest.fixture()
def cleanup():
    """登记本用例产生的 batch_id；结束时删任务行 + 文件行 + 磁盘文件。"""
    batches: list[str] = []
    yield batches

    with SessionLocal() as db:
        rows = (
            db.execute(select(ResumeFile).where(ResumeFile.upload_batch_id.in_(batches))).scalars().all()
            if batches
            else []
        )
        file_ids = [row.id for row in rows]
        paths = [resolve_stored_path(row.stored_path) for row in rows]
        if file_ids:
            db.execute(delete(ParseTask).where(ParseTask.resume_file_id.in_(file_ids)))
            db.execute(delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
            db.commit()

    for path in paths:
        path.unlink(missing_ok=True)


def upload(client: TestClient, auth: dict, cleanup: list[str], items: list[tuple[str, bytes]]):
    files = [("files", (name, data, "application/octet-stream")) for name, data in items]
    resp = client.post("/api/resumes/batch", files=files, headers=auth)
    if resp.status_code == 202 and resp.json().get("data", {}).get("upload_batch_id"):
        cleanup.append(resp.json()["data"]["upload_batch_id"])
    return resp


def test_mixed_pdf_docx_upload_success(client, auth, cleanup):
    pdf, docx = make_pdf(uuid4().hex), make_docx(uuid4().hex)

    resp = upload(client, auth, cleanup, [("张三.pdf", pdf), ("李四.DOCX", docx)])
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["code"] == 0

    data = body["data"]
    assert data["accepted"] == 2
    assert data["duplicates"] == []
    assert data["rejected"] == []
    assert len(data["upload_batch_id"]) == 26  # ULID
    assert data["queue_total"] >= 2

    with SessionLocal() as db:
        rows = (
            db.execute(select(ResumeFile).where(ResumeFile.upload_batch_id == data["upload_batch_id"]))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert {row.file_ext for row in rows} == {"pdf", "docx"}
        assert {row.filename for row in rows} == {"张三.pdf", "李四.DOCX"}  # 原始文件名保留
        assert all(len(row.file_hash) == 64 for row in rows)
        assert all(row.file_size > 0 for row in rows)
        assert all(row.uploaded_by > 0 for row in rows)
        # 落盘真实存在，且路径在 upload 根目录内
        for row in rows:
            path = resolve_stored_path(row.stored_path)
            assert path.exists(), row.stored_path
            assert path.is_relative_to(settings.upload_dir_path)
            assert path.name != row.filename  # 存储名是 UUID，不是用户文件名

        tasks = (
            db.execute(select(ParseTask).where(ParseTask.resume_file_id.in_([row.id for row in rows])))
            .scalars()
            .all()
        )
        assert len(tasks) == 2
        assert all(task.status == "pending" and task.stage is None and task.retries == 0 for task in tasks)


def test_same_content_other_filename_is_duplicate(client, auth, cleanup):
    tag = uuid4().hex
    pdf = make_pdf(tag)

    first = upload(client, auth, cleanup, [("张三.pdf", pdf)])
    assert first.status_code == 202
    first_data = first.json()["data"]
    assert first_data["accepted"] == 1

    with SessionLocal() as db:
        original = db.execute(select(ResumeFile).where(ResumeFile.file_hash == _sha(pdf))).scalar_one()
        original_task = db.execute(select(ParseTask).where(ParseTask.resume_file_id == original.id)).scalar_one()
        original_id, original_task_id = original.id, original_task.id

    # 改文件名重传同一份内容
    second = upload(client, auth, cleanup, [("张三-改名版.pdf", pdf)])
    assert second.status_code == 202, second.text
    data = second.json()["data"]
    assert data["accepted"] == 0
    assert data["rejected"] == []
    assert len(data["duplicates"]) == 1

    dup = data["duplicates"][0]
    assert dup["filename"] == "张三-改名版.pdf"
    assert dup["reason"] == "文件内容已存在"
    assert dup["resume_file_id"] == original_id
    assert dup["task_id"] == original_task_id
    assert dup["task_status"] == "pending"
    assert dup["can_requeue"] is False  # pending 不可重新入队

    # 库里仍然只有一行（没有新建文件行、没有第二条任务）
    with SessionLocal() as db:
        same_hash = db.execute(select(ResumeFile).where(ResumeFile.file_hash == _sha(pdf))).scalars().all()
        assert len(same_hash) == 1
        assert (
            len(db.execute(select(ParseTask).where(ParseTask.resume_file_id == original_id)).scalars().all()) == 1
        )


def test_can_requeue_true_for_failed_task(client, auth, cleanup):
    """duplicates 的 `can_requeue` 只对 cancelled/failed 为 true（接口 §4）。"""
    pdf = make_pdf(uuid4().hex)
    first = upload(client, auth, cleanup, [("王五.pdf", pdf)])
    assert first.status_code == 202

    with SessionLocal() as db:
        row = db.execute(select(ResumeFile).where(ResumeFile.file_hash == _sha(pdf))).scalar_one()
        db.execute(
            ParseTask.__table__.update().where(ParseTask.resume_file_id == row.id).values(status="failed")
        )
        db.commit()

    second = upload(client, auth, cleanup, [("王五.pdf", pdf)])
    dup = second.json()["data"]["duplicates"][0]
    assert dup["task_status"] == "failed"
    assert dup["can_requeue"] is True


def test_same_file_twice_in_one_request(client, auth, cleanup):
    """同一次 POST 里放两份相同内容：一份受理，一份进 duplicates。"""
    pdf = make_pdf(uuid4().hex)
    resp = upload(client, auth, cleanup, [("a.pdf", pdf), ("b.pdf", pdf)])
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["accepted"] == 1
    assert len(data["duplicates"]) == 1
    assert data["duplicates"][0]["filename"] == "b.pdf"


def test_fake_pdf_blocked_by_magic_bytes(client, auth, cleanup):
    """验收点 3：exe 改 .pdf 必须被拦；同批次的合法文件照常受理。"""
    docx = make_docx(uuid4().hex)
    resp = upload(client, auth, cleanup, [("简历.exe改的.pdf", make_fake_pdf()), ("正常.docx", docx)])
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]

    assert data["accepted"] == 1
    assert len(data["rejected"]) == 1
    assert data["rejected"][0]["filename"] == "简历.exe改的.pdf"
    assert data["rejected"][0]["reason"] == "文件内容不是有效的 PDF"


def test_all_rejected_returns_400(client, auth, cleanup):
    """全部非法 → 400；原因逐项写在 message 里（统一错误体无 data 字段）。"""
    resp = upload(client, auth, cleanup, [("a.pdf", make_fake_pdf()), ("b.txt", b"hello")])
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == 400
    assert body["data"] is None
    assert body["request_id"]
    assert "无任何有效文件" in body["message"]
    assert "a.pdf" in body["message"] and "b.txt" in body["message"]


def test_oversize_rejected(client, auth, cleanup):
    limit = settings.upload_max_mb * 1024 * 1024
    big = b"%PDF-1.4\n" + b"0" * limit  # 略超上限
    resp = upload(client, auth, cleanup, [("超大.pdf", big)])
    assert resp.status_code == 400, resp.text
    assert f"文件超过 {settings.upload_max_mb}MB" in resp.json()["message"]


def test_batch_summary_lists_upload(client, auth, cleanup):
    pdf, docx = make_pdf(uuid4().hex), make_docx(uuid4().hex)
    resp = upload(client, auth, cleanup, [("甲.pdf", pdf), ("乙.docx", docx)])
    batch_id = resp.json()["data"]["upload_batch_id"]

    summary = client.get("/api/resumes/batches", params={"page": 1, "page_size": 200}, headers=auth)
    assert summary.status_code == 200, summary.text
    body = summary.json()["data"]
    assert body["total"] >= 1

    item = next((row for row in body["items"] if row["upload_batch_id"] == batch_id), None)
    assert item is not None
    assert item["file_count"] == 2
    assert item["success"] == 0 and item["failed"] == 0
    assert item["pending"] == 2  # 一批刚上传、还没跑解析
    assert item["uploaded_at"]


def test_upload_requires_auth(client):
    resp = client.post("/api/resumes/batch", files=[("files", ("a.pdf", make_pdf("x"), "application/pdf"))])
    assert resp.status_code == 401
    assert resp.json()["code"] == 401


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
