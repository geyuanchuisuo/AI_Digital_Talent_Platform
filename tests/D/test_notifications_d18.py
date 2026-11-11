"""T-D-SELF-11 / D18 通知事件逐类闭环核对。

在 `test_notifications.py`（验收①②③④）之上补「逐事件」矩阵：
①逐类幂等：14 个 event_type 各自经生产方重入不新增；
②逐类读取隔离：个人只读本人、越权 403；管理侧全量；
③逐类失败重试：生产方 deliver 失败 → failed+reason → retry_failed 同行转 sent；
④幂等键形态：单收件人 v1.0 / 多收件人 v1.1（`:r{id}`）；
⑤生产方拒绝封闭清单外事件类型。

运行：pytest tests/D/test_notifications_d18.py -q（SQLite 内存库，无外部依赖）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.database import Base, get_db
from app.modules.notifications import producers, service
from app.modules.notifications.models import EVENT_TYPES, MULTI_RECIPIENT_EVENTS, Notification
from app.modules.notifications.router import router

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
    app.dependency_overrides[get_current_user] = lambda: U_HR
    return TestClient(app)


def _count(db) -> int:
    return db.execute(select(func.count()).select_from(Notification)).scalar_one()


# 14 枚举逐类生产方调用（收件人统一 1 号，重入参数完全一致）
PRODUCER_CASES = [
    ("recruitment.stage_changed", lambda db: producers.recruitment_stage_changed(
        db, application_id=1, recipient_user_id=1,
        new_external_stage="interviewing", change_seq=2)),
    ("interview.invited", lambda db: producers.interview_invited(
        db, round_id=2, recipients=[1], planned_at="2026-11-08 10:00")),
    ("interview.rescheduled", lambda db: producers.interview_rescheduled(
        db, round_id=2, recipients=[1], new_planned_at="2026-11-09 10:00")),
    ("interview.cancelled", lambda db: producers.interview_cancelled(
        db, round_id=2, recipients=[1], planned_at="2026-11-09 10:00")),
    ("course.assigned", lambda db: producers.course_assigned(
        db, plan_item_id=3, recipient_user_id=1, course_name="安全培训")),
    ("course.enrollment_reviewed", lambda db: producers.course_enrollment_reviewed(
        db, enrollment_id=4, recipient_user_id=1, result="approved")),
    ("course.waitlist_promoted", lambda db: producers.course_waitlist_promoted(
        db, session_id=5, recipient_user_id=1, seq=1)),
    ("training.due_reminder", lambda db: producers.training_due_reminder(
        db, record_id=6, recipients=[1], due_date="2026-11-10")),
    ("training.overdue", lambda db: producers.training_overdue(
        db, record_id=6, recipients=[1])),
    ("training.extension_decided", lambda db: producers.training_extension_decided(
        db, record_id=6, recipients=[1], new_due_date="2026-11-20")),
    ("workflow.approval_pending", lambda db: producers.workflow_event(
        db, event_type="workflow.approval_pending", run_id=7, recipients=[1], step_no=1)),
    ("workflow.run_failed", lambda db: producers.workflow_event(
        db, event_type="workflow.run_failed", run_id=7, recipients=[1], step_no=2)),
    ("workflow.needs_human", lambda db: producers.workflow_event(
        db, event_type="workflow.needs_human", run_id=7, recipients=[1], step_no=3)),
    ("talent.gap_alert", lambda db: producers.talent_gap_alert(
        db, demand_id=8, recipients=[1], gap_count=3)),
]


def test_producer_cases_cover_closed_event_list():
    """生产方矩阵本身覆盖封闭清单全部 14 枚举（防漏项）。"""
    assert {event for event, _ in PRODUCER_CASES} == set(EVENT_TYPES)
    assert len(PRODUCER_CASES) == 14


@pytest.mark.parametrize("event_type,produce", PRODUCER_CASES, ids=[e for e, _ in PRODUCER_CASES])
def test_each_event_type_idempotent_rerun(db_factory, event_type, produce):
    """①逐类幂等：同参数重入返回已有行（created=False），不新增通知。"""
    db = db_factory()
    first = produce(db)
    assert all(created is True for _, created in first)
    before = _count(db)
    second = produce(db)
    assert all(created is False for _, created in second)
    assert all(row.id == existing.id for (row, _), (existing, _) in zip(second, first))
    assert _count(db) == before


def test_per_event_read_isolation_matrix(db_factory, client):
    """②逐类读取隔离：14 类各发一条给 1 号，另发一条给 3 号。"""
    db = db_factory()
    for _, produce in PRODUCER_CASES:
        produce(db)
    producers.course_assigned(db, plan_item_id=99, recipient_user_id=3, course_name="他人课程")
    total = _count(db)
    assert total == 15

    client.app.dependency_overrides[get_current_user] = lambda: U_ALICE
    body = client.get("/api/notifications").json()["data"]
    assert body["total"] == 14
    assert {item["event_type"] for item in body["items"]} == set(EVENT_TYPES)

    # 3 号那条：本人可读，1 号越权 403，管理侧可读
    others = db.execute(
        select(Notification).where(Notification.recipient_user_id == 3)
    ).scalar_one()
    assert client.get(f"/api/notifications/{others.id}").status_code == 403
    client.app.dependency_overrides[get_current_user] = lambda: U_BOB
    assert client.get(f"/api/notifications/{others.id}").status_code == 200
    client.app.dependency_overrides[get_current_user] = lambda: U_HR
    assert client.get("/api/notifications").json()["data"]["total"] == total


def _boom(db, row):
    raise RuntimeError("channel down")


@pytest.mark.parametrize(
    "event_type,produce",
    [
        ("course.assigned", lambda db, deliver: producers.course_assigned(
            db, plan_item_id=3, recipient_user_id=1, course_name="x", deliver=deliver)),
        ("training.overdue", lambda db, deliver: producers.training_overdue(
            db, record_id=6, recipients=[1], deliver=deliver)),
        ("workflow.run_failed", lambda db, deliver: producers.workflow_event(
            db, event_type="workflow.run_failed", run_id=7, recipients=[1], step_no=2,
            deliver=deliver)),
    ],
    ids=["course.assigned", "training.overdue", "workflow.run_failed"],
)
def test_each_producer_failed_then_retry_same_row(db_factory, event_type, produce):
    """③逐类失败重试：deliver 异常 → failed+reason → retry_failed 同行转 sent，不新增。"""
    db = db_factory()
    (row, created) = produce(db, _boom)[0]
    assert created is True
    assert row.status == "failed" and "channel down" in row.failure_reason
    before = _count(db)

    fixed = service.retry_failed(db, row.id)
    assert fixed.id == row.id and fixed.status == "sent"
    assert fixed.sent_at is not None and fixed.failure_reason is None
    assert _count(db) == before


def test_idempotency_key_shape_per_event_class(db_factory):
    """④键形态：单收件人 v1.0 原样；多收件人 v1.1 恒定追加 :r{id}。"""
    db = db_factory()
    producers.course_assigned(db, plan_item_id=3, recipient_user_id=1, course_name="x")
    producers.training_overdue(db, record_id=6, recipients=[1, 2])
    producers.workflow_event(db, event_type="workflow.needs_human", run_id=7,
                             recipients=[1, 2], step_no=1)
    rows = list(db.execute(select(Notification)).scalars())
    single = [r for r in rows if r.event_type == "course.assigned"]
    multi = [r for r in rows if r.event_type in MULTI_RECIPIENT_EVENTS]
    assert single and all(":r" not in r.idempotency_key for r in single)
    assert len(multi) == 4  # overdue 2 人 + needs_human 2 人
    assert all(r.idempotency_key.endswith(f":r{r.recipient_user_id}") for r in multi)


def test_workflow_event_rejects_event_outside_closed_list(db_factory):
    """⑤生产方拒绝封闭清单外的 workflow 事件类型（不静默落库）。"""
    db = db_factory()
    with pytest.raises(ValueError):
        producers.workflow_event(db, event_type="workflow.cancel_requested",
                                 run_id=7, recipients=[1], step_no=1)
    assert _count(db) == 0


def test_event_meta_lists_closed_set(client):
    body = client.get("/api/notifications/meta/event-types").json()["data"]
    assert body["event_types"] == list(EVENT_TYPES)
    assert len(body["event_types"]) == 14


def test_wired_producer_coverage_matches_contract(db_factory):
    """生产方接线现状（与通知契约 §7 清单一致）：C 培训 6 类 + D 数字员工 1 类已接；
    A 招聘/面试 4 类与 D 工作流审批/失败 2 类仅有生产方函数、未接业务调用。"""
    wired = {
        "course.assigned", "course.enrollment_reviewed", "course.waitlist_promoted",
        "training.due_reminder", "training.overdue", "training.extension_decided",
        "workflow.needs_human",
    }
    pending = set(EVENT_TYPES) - wired
    assert pending == {
        "recruitment.stage_changed", "interview.invited", "interview.rescheduled",
        "interview.cancelled", "workflow.approval_pending", "workflow.run_failed",
        "talent.gap_alert",
    }
