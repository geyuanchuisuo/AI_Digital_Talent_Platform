"""T-11 L2：上传落盘 → 按 `stored_path` 提取真实文件的闭环（真实 MySQL + 真实磁盘）。

只验证"提取器能直接吃上传链路产出的文件"这一个契约：
`resume_files.stored_path` 是相对 `backend/` 的 POSIX 路径 → `resolve_stored_path()`
还原成绝对路径 → `extract_text()` 读出中文正文。不做 LLM、不做解析编排（T-13）。
"""

from __future__ import annotations

import io
from uuid import uuid4

import docx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import ParseTask, ResumeFile
from app.service.extract_service import extract_text
from app.service.upload_service import resolve_stored_path

pytestmark = pytest.mark.integration


def make_resume_docx(tag: str) -> bytes:
    document = docx.Document()
    document.add_paragraph(f"姓名：候选人{tag}")
    document.add_paragraph("求职意向：后端开发工程师")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "时间"
    table.rows[0].cells[1].text = "公司"
    table.rows[1].cells[0].text = "2020-2023"
    table.rows[1].cells[1].text = "某互联网公司"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


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
    batch_ids: list[str] = []
    yield batch_ids

    with SessionLocal() as db:
        rows = (
            db.execute(select(ResumeFile).where(ResumeFile.upload_batch_id.in_(batch_ids))).scalars().all()
            if batch_ids
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


def test_extract_uploaded_docx_end_to_end(client, auth, cleanup):
    tag = uuid4().hex[:8]
    resp = client.post(
        "/api/resumes/batch",
        files=[("files", (f"候选人{tag}.docx", make_resume_docx(tag), "application/octet-stream"))],
        headers=auth,
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["data"]["upload_batch_id"]
    cleanup.append(batch_id)

    with SessionLocal() as db:
        row = db.execute(select(ResumeFile).where(ResumeFile.upload_batch_id == batch_id)).scalar_one()
        stored_path = row.stored_path
        file_ext = row.file_ext

    # 入库的是相对路径，必须靠 resolve_stored_path 还原（换机器/换盘符也能用）
    assert not stored_path.startswith("/") and ":" not in stored_path

    result = extract_text(resolve_stored_path(stored_path), ext=file_ext)
    assert f"候选人{tag}" in result.text
    assert "后端开发工程师" in result.text
    assert "2020-2023\t某互联网公司" in result.text
    assert result.truncated is False
