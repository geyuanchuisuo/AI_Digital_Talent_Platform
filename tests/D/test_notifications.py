"""通知中心用例（T-D03 验收①②③④）。

①同幂等键重复投递被唯一约束拦截；②个人只收本人、越权读取 403；
③12 类（14 枚举）事件发信单测；④失败→failed+reason→重试不产生新记录。
运行：pytest tests/D/test_notifications.py -v（SQLite 内存库，无 MySQL 依赖；
conftest.py 自动加载，其 SQLite 编译规则使 BIGINT 主键可自增）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.errors import BizError, register_exception_handlers
from app.database import Base, get_db
from app.modules.notifications import producers, service
from app.modules.notifications.models import EVENT_TYPES, Notification
from app.modules.notifications.router import router

# ---- 账号替身（阶段 1 三值 role；get_account_scope 只读 role/person_id）----
U_ALICE = SimpleNamespace(id=1, role="personal", person_id=None)
U_BOB = SimpleNamespace(id=3, role="personal", person_id=None)
U_HR = SimpleNamespace(id=2, role="hr", person_id=None)


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Notification.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(db_factory):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    def override_db():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _as(client: TestClient, user):
    client.app.dependency_overrides[get_current_user] = lambda: user
    return client


def _count(db) -> int:
    return db.execute(select(func.count()).select_from(Notification)).scalar_one()


# ---- 验收①：幂等投递 ----

def test_emit_same_key_returns_same_row(db_factory):
    db = db_factory()
    row1, created1 = service.emit(
        db, event_type="recruitment.stage_changed", ref_type="application", ref_id=9,
        recipient_user_id=1, idempotency_key="recruitment.stage_changed:9:closed:1",
        payload={"external_stage": "closed"},
    )
    row2, created2 = service.emit(
        db, event_type="recruitment.stage_changed", ref_type="application", ref_id=9,
        recipient_user_id=1, idempotency_key="recruitment.stage_changed:9:closed:1",
        payload={"external_stage": "closed"},
    )
    assert created1 is True and created2 is False
    assert row2.id == row1.id
    assert _count(db) == 1


def test_unique_constraint_blocks_duplicate_direct_insert(db_factory):
    """数据库级保证：绕过 service 预查直接插同键第二行 → 唯一约束拦截。"""
    db = db_factory()
    db.add(Notification(event_type="course.assigned", ref_type="training_plan_item",
                        ref_id=5, recipient_user_id=1, idempotency_key="course.assigned:5"))
    db.commit()
    db.add(Notification(event_type="course.assigned", ref_type="training_plan_item",
                        ref_id=5, recipient_user_id=1, idempotency_key="course.assigned:5"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---- 验收②：读取隔离与越权 403 ----

def test_personal_reads_only_own_and_others_detail_403(db_factory, client):
    db = db_factory()
    service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                 ref_id=1, recipient_user_id=1, idempotency_key="course.assigned:1")
    service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                 ref_id=2, recipient_user_id=1, idempotency_key="course.assigned:2")
    service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                 ref_id=3, recipient_user_id=3, idempotency_key="course.assigned:3")

    _as(client, U_ALICE)
    body = client.get("/api/notifications").json()["data"]
    assert body["total"] == 2
    assert all(item["recipient_user_id"] == 1 for item in body["items"])

    others = client.get("/api/notifications").json()["data"]["items"]
    other_id = others[0]["id"] + 100  # 不存在的 id 也统一 404 语义；先造一条他人通知验证 403
    db_row = db.execute(
        select(Notification).where(Notification.recipient_user_id == 3)
    ).scalar_one()
    assert db_row.id != other_id
    resp = client.get(f"/api/notifications/{db_row.id}")
    assert resp.status_code == 403
    resp = client.post(f"/api/notifications/{db_row.id}/read")
    assert resp.status_code == 403
    resp = client.get("/api/notifications", params={"recipient_user_id": 3})
    assert resp.status_code == 403  # personal 不可按收件人过滤


def test_personal_get_unknown_id_404(db_factory, client):
    _as(client, U_ALICE)
    assert client.get("/api/notifications/99999").status_code == 404


def test_hr_sees_all_and_filters_by_recipient(db_factory, client):
    db = db_factory()
    service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                 ref_id=1, recipient_user_id=1, idempotency_key="k1")
    service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                 ref_id=2, recipient_user_id=3, idempotency_key="k2")
    _as(client, U_HR)
    assert client.get("/api/notifications").json()["data"]["total"] == 2
    body = client.get("/api/notifications", params={"recipient_user_id": 3}).json()["data"]
    assert body["total"] == 1 and body["items"][0]["recipient_user_id"] == 3


def test_mark_read_only_recipient_and_idempotent(db_factory, client):
    db = db_factory()
    (row, _) = service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                            ref_id=1, recipient_user_id=1, idempotency_key="k1")
    _as(client, U_ALICE)
    body = client.post(f"/api/notifications/{row.id}/read").json()["data"]
    assert body["status"] == "read" and body["read_at"]
    again = client.post(f"/api/notifications/{row.id}/read").json()["data"]
    assert again["status"] == "read"  # 幂等，不报错
    # 管理侧也不能代读（已读是收件人行为）
    _as(client, U_HR)
    assert client.post(f"/api/notifications/{row.id}/read").status_code == 403


# ---- 验收③：12 类（14 枚举）事件发信 ----

def test_all_twelve_categories_fourteen_event_types(db_factory):
    db = db_factory()
    producers.recruitment_stage_changed(db, application_id=1, recipient_user_id=1,
                                        new_external_stage="submitted", change_seq=1)
    producers.interview_invited(db, round_id=2, recipients=[1], planned_at="2026-11-08 10:00")
    producers.interview_rescheduled(db, round_id=2, recipients=[1], new_planned_at="2026-11-09 10:00")
    producers.interview_cancelled(db, round_id=2, recipients=[1], planned_at="2026-11-09 10:00")
    producers.course_assigned(db, plan_item_id=3, recipient_user_id=1, course_name="安全培训")
    producers.course_enrollment_reviewed(db, enrollment_id=4, recipient_user_id=1, result="approved")
    producers.course_waitlist_promoted(db, session_id=5, recipient_user_id=1, seq=1)
    producers.training_due_reminder(db, record_id=6, recipients=[1, 2], due_date="2026-11-10")
    producers.training_overdue(db, record_id=6, recipients=[1, 2])
    producers.training_extension_decided(db, record_id=6, recipients=[1, 2], new_due_date="2026-11-20")
    producers.workflow_event(db, event_type="workflow.approval_pending", run_id=7,
                             recipients=[2], step_no=1)
    producers.workflow_event(db, event_type="workflow.run_failed", run_id=7,
                             recipients=[2], step_no=2)
    producers.workflow_event(db, event_type="workflow.needs_human", run_id=7,
                             recipients=[2], step_no=3)
    producers.talent_gap_alert(db, demand_id=8, recipients=[2, 3], gap_count=3)

    rows = list(db.execute(select(Notification)).scalars())
    types = {r.event_type for r in rows}
    assert types == set(EVENT_TYPES)  # 14 枚举全覆盖
    assert len(rows) == 18  # 单收件人 10（招聘1+面试3+课程3+工作流3）+ 多收件人 8（培训 2×3 + 缺口 2）
    # 多收件人事件每人一条且键不同（v1.1 规则：恒定追加 :r{id}）
    overdue = [r for r in rows if r.event_type == "training.overdue"]
    assert len(overdue) == 2 and overdue[0].idempotency_key != overdue[1].idempotency_key
    assert all(rid in {1, 2} for rid in [r.recipient_user_id for r in overdue])
    assert all(r.status == "sent" and r.sent_at for r in rows)


def test_single_recipient_events_keep_v1_key_and_idempotent(db_factory):
    """单收件人事件保持 v1.0 键格式（不带 :r）；生产方重入不产生第二行。"""
    db = db_factory()
    row1, c1 = producers.course_assigned(db, plan_item_id=3, recipient_user_id=1, course_name="x")[0]
    row2, c2 = producers.course_assigned(db, plan_item_id=3, recipient_user_id=1, course_name="x")[0]
    assert c1 and not c2 and row1.id == row2.id
    assert row1.idempotency_key == "course.assigned:3"


# ---- 验收④：失败 → failed+reason → 重试不产生新记录 ----

def _boom(db, row):
    raise RuntimeError("channel down")


def test_failed_delivery_then_retry_no_new_row(db_factory):
    db = db_factory()
    row, _ = service.emit(db, event_type="workflow.run_failed", ref_type="workflow_run",
                          ref_id=7, recipient_user_id=2,
                          idempotency_key="workflow.run_failed:7:2:r2", deliver=_boom)
    assert row.status == "failed" and "channel down" in row.failure_reason

    # 同键重入：仍同一行，不新增（幂等重发语义）
    row2, created = service.emit(db, event_type="workflow.run_failed", ref_type="workflow_run",
                                 ref_id=7, recipient_user_id=2,
                                 idempotency_key="workflow.run_failed:7:2:r2", deliver=_boom)
    assert not created and row2.id == row.id
    assert _count(db) == 1

    # 重试成功：同一行转 sent，仍不新增
    fixed = service.retry_failed(db, row.id)
    assert fixed.status == "sent" and fixed.sent_at is not None and fixed.failure_reason is None
    assert _count(db) == 1


def test_retry_only_failed_status(db_factory):
    db = db_factory()
    row, _ = service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                          ref_id=1, recipient_user_id=1, idempotency_key="k1")
    with pytest.raises(BizError) as ei:
        service.retry_failed(db, row.id)
    assert ei.value.code == 400
    with pytest.raises(BizError) as ei2:
        service.retry_failed(db, 99999)
    assert ei2.value.code == 404


def test_unknown_event_type_rejected(db_factory):
    db = db_factory()
    with pytest.raises(BizError) as ei:
        service.emit(db, event_type="marketing.promo", ref_type="x", ref_id=1,
                     recipient_user_id=1, idempotency_key="bad:1")
    assert ei.value.code == 422  # F09 清单外不发送


def test_failure_reason_hidden_for_non_failed(db_factory, client):
    db = db_factory()
    (row, _) = service.emit(db, event_type="course.assigned", ref_type="training_plan_item",
                            ref_id=1, recipient_user_id=1, idempotency_key="k1")
    _as(client, U_ALICE)
    body = client.get(f"/api/notifications/{row.id}").json()["data"]
    assert body["failure_reason"] is None and body["status"] == "sent"
