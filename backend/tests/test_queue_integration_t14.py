"""T-14 L2：队列管理集成测试（真实 MySQL，不外呼 LLM/Embedding/Milvus）。

覆盖 todolist T-14 四个验收点：
1. 接口与 DB 状态一致（列表/汇总/筛选）；
2. **retry 保留 stage**，**requeue 清空 stage**；
3. 改并发写入 `sys_settings`（重启保持由 Layer 4 的 runner 周期读取保证）；
4. 删除（软取消）后 requeue 能重新入队且不违反 `uk_resume_file`；running 删除被拒。

隔离方式：每个用例自建一个唯一 `upload_batch_id` 的文件+任务行，结束时按批次清理。
TaskRunner 全局单例已被 conftest 打成空操作，`pending` 不会被真实消费。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from ulid import ULID

from app.config import settings
from app.database import SessionLocal
from app.dao import sys_settings as sys_setting_dao
from app.models import ParseTask, ParsedResume, ResumeFile, User

pytestmark = pytest.mark.integration


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


@pytest.fixture(scope="module")
def user_id():
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "hr")).scalar_one()
        return user.id


@pytest.fixture()
def batches():
    """本用例创建的批次；结束时删解析结果 → 任务 → 文件行。"""
    created: list[str] = []
    yield created

    with SessionLocal() as db:
        files = (
            db.execute(select(ResumeFile).where(ResumeFile.upload_batch_id.in_(created))).scalars().all()
            if created
            else []
        )
        file_ids = [row.id for row in files]
        if file_ids:
            db.execute(delete(ParsedResume).where(ParsedResume.resume_file_id.in_(file_ids)))
            db.execute(delete(ParseTask).where(ParseTask.resume_file_id.in_(file_ids)))
            db.execute(delete(ResumeFile).where(ResumeFile.id.in_(file_ids)))
            db.commit()


def new_batch(batches: list[str]) -> str:
    batch = str(ULID())
    batches.append(batch)
    return batch


def create_task(
    batch: str,
    *,
    user_id: int,
    status: str,
    stage: str | None = None,
    error: str | None = None,
    retries: int = 0,
    talent_profile_id: int | None = None,
    started: bool = False,
) -> tuple[int, int]:
    """建 1 条 resume_files + parse_tasks（+ 可选 parsed_resumes），返回 (file_id, task_id)。"""
    with SessionLocal() as db:
        file = ResumeFile(
            filename=f"t14-{uuid4().hex[:8]}.pdf",
            stored_path=f"uploads/t14/{uuid4().hex}.pdf",
            file_hash=uuid4().hex * 2,
            file_ext="pdf",
            file_size=1,
            upload_batch_id=batch,
            uploaded_by=user_id,
        )
        db.add(file)
        db.flush()

        task = ParseTask(
            resume_file_id=file.id,
            status=status,
            stage=stage,
            error=error,
            retries=retries,
            truncated=0,
        )
        if started:
            task.started_at = datetime(2026, 11, 5, 9, 0, 0)
            task.heartbeat_at = datetime(2026, 11, 5, 9, 0, 0)
        db.add(task)
        db.flush()

        if talent_profile_id is not None:
            db.add(
                ParsedResume(
                    resume_file_id=file.id,
                    talent_profile_id=talent_profile_id,
                    structured_json={"basic": {"name": "张三"}},
                    raw_text="raw",
                )
            )
        db.commit()
        return file.id, task.id


def task_row(task_id: int) -> ParseTask:
    with SessionLocal() as db:
        return db.get(ParseTask, task_id)


def test_queue_list_summary_and_filter(client, auth, user_id, batches):
    batch = new_batch(batches)
    _, pending_id = create_task(batch, user_id=user_id, status="pending")
    _, failed_id = create_task(
        batch, user_id=user_id, status="failed", stage="vector", error="Milvus 连接失败", retries=1
    )
    _, cancelled_id = create_task(batch, user_id=user_id, status="cancelled")

    resp = client.get(f"/api/resumes/queue?upload_batch_id={batch}", headers=auth)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["summary"] == {
        "pending": 1,
        "running": 0,
        "success": 0,
        "failed": 1,
        "duplicate_pending": 0,
        "cancelled": 1,
        "total": 3,
    }
    assert data["total"] == 3
    assert data["page"] == 1 and data["page_size"] == 20
    assert 1 <= data["concurrency"] <= 5
    assert len(data["items"]) == 3

    by_id = {item["task_id"]: item for item in data["items"]}
    assert by_id[pending_id]["can_retry"] is False
    assert by_id[pending_id]["can_requeue"] is False
    failed = by_id[failed_id]
    assert failed["status"] == "failed"
    assert failed["stage"] == "vector"
    assert failed["error"] == "Milvus 连接失败"
    assert failed["retries"] == 1
    assert failed["can_retry"] is True and failed["can_requeue"] is True
    assert by_id[cancelled_id]["can_requeue"] is True

    # status 只筛列表，summary 保持批次全量（状态 tab 角标需要全量计数）
    filtered = client.get(f"/api/resumes/queue?upload_batch_id={batch}&status=failed", headers=auth)
    assert filtered.status_code == 200, filtered.text
    filtered_data = filtered.json()["data"]
    assert filtered_data["total"] == 1
    assert filtered_data["summary"]["total"] == 3
    assert [item["task_id"] for item in filtered_data["items"]] == [failed_id]


def test_queue_filters_by_status_and_validates_unknown(client, auth, user_id, batches):
    batch = new_batch(batches)
    create_task(batch, user_id=user_id, status="pending")

    resp = client.get(f"/api/resumes/queue?upload_batch_id={batch}&status=unknown", headers=auth)
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == 400


def test_retry_keeps_stage_and_only_failed(client, auth, user_id, batches):
    batch = new_batch(batches)
    _, task_id = create_task(
        batch, user_id=user_id, status="failed", stage="vector", error="Milvus 连接失败", retries=1
    )

    resp = client.post(f"/api/resumes/queue/{task_id}/retry", headers=auth)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data == {"task_id": task_id, "status": "pending", "stage": "vector", "retries": 2}

    row = task_row(task_id)
    assert row.status == "pending"
    assert row.stage == "vector"  # 断点续跑：stage 必须保留
    assert row.error is None
    assert row.started_at is None and row.heartbeat_at is None and row.finished_at is None

    # 非 failed 不能 retry
    assert client.post(f"/api/resumes/queue/{task_id}/retry", headers=auth).status_code == 400
    assert client.post("/api/resumes/queue/999999/retry", headers=auth).status_code == 404


def test_requeue_clears_stage_for_cancelled_and_failed(client, auth, user_id, batches):
    batch = new_batch(batches)
    _, cancelled_id = create_task(batch, user_id=user_id, status="cancelled", stage="extract", retries=1)
    _, failed_id = create_task(batch, user_id=user_id, status="failed", stage="profile", retries=2)

    resp = client.post(f"/api/resumes/queue/{cancelled_id}/requeue", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["stage"] is None
    row = task_row(cancelled_id)
    assert row.status == "pending" and row.stage is None and row.retries == 2

    resp = client.post(f"/api/resumes/queue/{failed_id}/requeue", headers=auth)
    assert resp.status_code == 200, resp.text
    row = task_row(failed_id)
    assert row.status == "pending" and row.stage is None and row.retries == 3

    # pending 不可 requeue；不存在 404
    assert client.post(f"/api/resumes/queue/{failed_id}/requeue", headers=auth).status_code == 400
    assert client.post("/api/resumes/queue/999999/requeue", headers=auth).status_code == 404


def test_delete_is_soft_cancel_and_requeue_restores(client, auth, user_id, batches):
    batch = new_batch(batches)
    _, running_id = create_task(batch, user_id=user_id, status="running", stage="extract", started=True)
    _, pending_id = create_task(batch, user_id=user_id, status="pending")

    # running 拒绝
    resp = client.delete(f"/api/resumes/queue/{running_id}", headers=auth)
    assert resp.status_code == 400, resp.text
    assert task_row(running_id).status == "running"

    # pending 软取消：只改状态，不删文件/任务行（1:1 唯一键不受影响）
    resp = client.delete(f"/api/resumes/queue/{pending_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "cancelled"
    with SessionLocal() as db:
        row = db.get(ParseTask, pending_id)
        assert row is not None and row.status == "cancelled"
        assert db.get(ResumeFile, row.resume_file_id) is not None

    # 已 cancelled 再删 → 404（活动队列中已不存在）
    assert client.delete(f"/api/resumes/queue/{pending_id}", headers=auth).status_code == 404
    assert client.delete("/api/resumes/queue/999999", headers=auth).status_code == 404

    # 恢复入口：requeue 重新入队，不新插行
    resp = client.post(f"/api/resumes/queue/{pending_id}/requeue", headers=auth)
    assert resp.status_code == 200, resp.text
    with SessionLocal() as db:
        assert db.get(ParseTask, pending_id).status == "pending"
        files = db.execute(
            select(ResumeFile).where(ResumeFile.upload_batch_id == batch)
        ).scalars().all()
        assert len(files) == 2  # 没有因为 requeue 多插文件/任务


@pytest.fixture()
def restore_concurrency():
    """PATCH settings 会改真实库；用例结束恢复原值，避免污染其他测试。"""
    with SessionLocal() as db:
        original = sys_setting_dao.get_int(db, "task_concurrency", int(settings.task_concurrency))
    yield original
    with SessionLocal() as db:
        sys_setting_dao.set_value(db, "task_concurrency", str(original), updated_by=None)
        db.commit()
    settings.task_concurrency = original


def test_queue_settings_writes_sys_settings(client, auth, restore_concurrency):
    resp = client.patch("/api/resumes/queue/settings", json={"concurrency": 4}, headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"concurrency": 4, "effective": "next_scan"}

    with SessionLocal() as db:
        assert sys_setting_dao.get_int(db, "task_concurrency", -1) == 4

    # GET /queue 的 concurrency 同源（取 sys_settings）
    assert client.get("/api/resumes/queue", headers=auth).json()["data"]["concurrency"] == 4

    # 模拟重启：运行时值打回 .env 默认，再执行启动时的 load_runtime_overrides → 仍读到 4
    # （验收点"改并发后重启服务仍是新值"的可自动化部分）
    settings.task_concurrency = 2
    settings.load_runtime_overrides()
    assert settings.task_concurrency == 4

    for invalid in (0, 6):
        resp = client.patch("/api/resumes/queue/settings", json={"concurrency": invalid}, headers=auth)
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == 400


def test_queue_requires_auth(client):
    assert client.get("/api/resumes/queue").status_code == 401
