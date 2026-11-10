"""T-D12 问数反馈/纠错入口用例。

验收：①反馈落库且关联快照 ②反馈不改当次结果、不自动改统计 ③越权反馈他人结果 403；
列表管理侧可见；幂等（重复标记不新增）。
运行：pytest tests/D/test_analytics_td12.py -v（SQLite 内存库）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.cache import CacheClient, reset_cache
from app.core.errors import BizError, register_exception_handlers
from app.database import Base, get_db
from app.models import (
    AnalyticsQuerySnapshot,
    Application,
    JobProfile,
    MetricFeedback,
    MetricRegistry,
    ProjectDemand,
)
from app.modules.analytics import service
from app.modules.analytics.feedback import service as feedback_service
from app.modules.analytics.models import seed_metric_registry
from app.modules.analytics.router import router

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_HR2 = SimpleNamespace(id=4, role="hr", person_id=None)  # 另一管理侧用户
U_PERSONAL = SimpleNamespace(id=5, role="personal", person_id=1)

CORE_TABLES = (
    JobProfile.__table__,
    ProjectDemand.__table__,
    Application.__table__,
    MetricRegistry.__table__,
    AnalyticsQuerySnapshot.__table__,
    MetricFeedback.__table__,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=CORE_TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    # Align fixture authentication with the shared A06 database.
    from app.models import SysRole, SysUserRole, UserScopeGrant
    Base.metadata.create_all(engine, tables=[SysRole.__table__, SysUserRole.__table__, UserScopeGrant.__table__])
    monkeypatch.setattr('app.database.SessionLocal', factory)
    from app.models import User as ScopeUser
    ScopeUser.__table__.create(factory.kw['bind'], checkfirst=True)
    with factory() as scope_db:
        for key, value in list(globals().items()):
            if key.startswith('U_') and isinstance(getattr(value, 'id', None), int) and hasattr(value, 'role'):
                if scope_db.get(ScopeUser, value.id) is None:
                    scope_db.add(ScopeUser(id=value.id, username='scope_fixture_'+str(value.id),
                        password_hash='test-only', role=value.role, person_id=getattr(value, 'person_id', None)))
        scope_db.commit()

    seed_metric_registry(engine)

    monkeypatch.setattr(service, "_replica_engine", engine)
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(feedback_service, "SessionLocal", factory)
    fake = CacheClient("redis://fake", client=__import__("fake_redis", fromlist=["FakeRedis"]).FakeRedis())
    reset_cache(fake)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    def override_db():
        db2 = factory()
        try:
            yield db2
        finally:
            db2.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.app.dependency_overrides[get_current_user] = lambda: U_HR
    yield SimpleNamespace(engine=engine, factory=factory, cache=fake, app=app, client=client, monkeypatch=monkeypatch)
    reset_cache(None)
    engine.dispose()


def _as(client: TestClient, user):
    client.app.dependency_overrides[get_current_user] = lambda: user
    return client


def _make_job(db, title: str, version: int, active: int, threshold: str = "60.00") -> JobProfile:
    row = JobProfile(title=title, version=version, is_active=active,
                     jd_text="x", requirements_text="x", hard_rule_switches_json={},
                     weights_json={}, pass_threshold=threshold)
    db.add(row)
    db.flush()
    return row


def _make_snapshot(env, user, metric_id="jobs.active") -> int:
    with env.factory() as db:
        row = AnalyticsQuerySnapshot(
            metric_id=metric_id, params_json=None, data_time=None,
            result_json={"value": 4}, source="primary", cache_hit=0,
            created_by=user.id,
        )
        db.add(row)
        db.commit()
        return int(row.id)


def _run(coro):
    return asyncio.run(coro)


# ---- 验收①：反馈落库且关联快照 ----

def test_feedback_persisted_and_linked_to_snapshot(env):
    snap_id = _make_snapshot(env, U_HR)
    row = _run(feedback_service.create_feedback(U_HR, snap_id, "数字与招聘系统对不上"))
    assert row["snapshot_id"] == snap_id and row["created_by"] == 2
    assert row["comment"] == "数字与招聘系统对不上"
    with env.factory() as db:
        f = db.get(MetricFeedback, row["id"])
        assert f.snapshot_id == snap_id  # 关联快照
        snap = db.get(AnalyticsQuerySnapshot, snap_id)
        assert snap is not None and snap.created_by == 2


def test_repeated_mark_is_idempotent(env):
    snap_id = _make_snapshot(env, U_HR)
    r1 = _run(feedback_service.create_feedback(U_HR, snap_id, "第一次"))
    r2 = _run(feedback_service.create_feedback(U_HR, snap_id, "第二次"))
    assert r1["id"] == r2["id"]  # 幂等：不新增
    with env.factory() as db:
        rows = list(db.execute(select(MetricFeedback)).scalars())
    assert len(rows) == 1


def test_feedback_list_admin_visible_with_snapshot_link(env):
    snap1 = _make_snapshot(env, U_HR)
    snap2 = _make_snapshot(env, U_HR, metric_id="job.demand_progress_gap")
    _run(feedback_service.create_feedback(U_HR, snap1, "不对"))
    _run(feedback_service.create_feedback(U_HR, snap2, None))
    items = _run(feedback_service.list_feedback(U_HR))
    assert len(items) == 2
    assert all("snapshot" in i and i["snapshot"]["metric_id"] for i in items)


# ---- 验收②：反馈不改当次结果、不自动改统计 ----

def test_feedback_does_not_change_metric_result(env):
    """验收②：反馈前后同查询结果一致（反馈不自动改统计）。"""
    with env.factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        db.add(ProjectDemand(position_name="需求A", headcount=5, status="open",
                             job_profile_id=job.id, project_group_id=1001))
        db.commit()
    r1 = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    snap_id = r1.snapshot_id
    _run(feedback_service.create_feedback(U_HR, snap_id, "这个数不对"))
    r2 = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    assert r1.value == r2.value and r1.table == r2.table  # 反馈零影响
    with env.factory() as db:
        snaps = list(db.execute(select(AnalyticsQuerySnapshot)).scalars())
    assert all(s.result_json["value"] == r1.value for s in snaps)  # 快照值也未变


# ---- 验收③：越权反馈他人结果 403 ----

def test_feedback_on_others_snapshot_403(env):
    snap_id = _make_snapshot(env, U_HR)  # U_HR 创建
    # 另一管理侧用户反馈他人快照 → 403
    with pytest.raises(BizError) as ei:
        _run(feedback_service.create_feedback(U_HR2, snap_id, "我也要说两句"))
    assert ei.value.code == 403
    with env.factory() as db:
        rows = list(db.execute(select(MetricFeedback)).scalars())
    assert rows == []  # 未落库


def test_feedback_unknown_snapshot_404(env):
    with pytest.raises(BizError) as ei:
        _run(feedback_service.create_feedback(U_HR, 99999, "x"))
    assert ei.value.code == 404


def test_personal_denied_on_feedback_endpoints(env):
    snap_id = _make_snapshot(env, U_HR)
    with pytest.raises(BizError) as ei:
        _run(feedback_service.create_feedback(U_PERSONAL, snap_id, None))
    assert ei.value.code == 403
    with pytest.raises(BizError) as ei2:
        _run(feedback_service.list_feedback(U_PERSONAL))
    assert ei2.value.code == 403


# ---- HTTP 端点 ----

def test_feedback_http_endpoints(env):
    snap_id = _make_snapshot(env, U_HR)
    resp = env.client.post("/api/analytics/feedback",
                           json={"snapshot_id": snap_id, "comment": "口径存疑"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["snapshot_id"] == snap_id
    resp2 = env.client.get("/api/analytics/feedback")
    assert resp2.status_code == 200
    items = resp2.json()["data"]["items"]
    assert len(items) == 1 and items[0]["snapshot"]["metric_id"] == "jobs.active"


def test_feedback_http_personal_403(env):
    snap_id = _make_snapshot(env, U_HR)
    env.client.app.dependency_overrides[get_current_user] = lambda: U_PERSONAL
    assert env.client.post("/api/analytics/feedback",
                           json={"snapshot_id": snap_id}).status_code == 403
    assert env.client.get("/api/analytics/feedback").status_code == 403
