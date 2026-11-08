"""问数切片用例（T-D01b 验收）：指标计算/越权反例/缓存命中/快照落库/SSE。

SQLite 内存库隔离；真实副本一致性用例在文件尾（真实 MySQL→真实 PG，不可达自动 skip）。
运行：pytest tests/D/test_analytics_slice.py -v
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

from app.core.cache import CacheClient, reset_cache
from app.core.errors import BizError, register_exception_handlers
from app.database import Base, get_db
from app.models import AnalyticsQuerySnapshot, JobProfile, MetricRegistry
from app.modules.analytics import service
from app.modules.analytics.models import seed_metric_registry
from app.modules.analytics.router import router

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=1)

# 口径样例（A 交付 §1）：按 title 去重取最新 version，再数 is_active=1
# 后端 v2 未启用（最新版不算）、前端 v1 启用（算）、算法未启用（不算）→ 期望 1
SEED_JOBS = [
    ("后端工程师", 1, 1),
    ("后端工程师", 2, 0),
    ("前端工程师", 1, 1),
    ("算法工程师", 1, 0),
]
EXPECTED_ACTIVE = 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[JobProfile.__table__, AnalyticsQuerySnapshot.__table__, MetricRegistry.__table__],
    )
    seed_metric_registry(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for title, version, active in SEED_JOBS:
            db.add(
                JobProfile(
                    title=title,
                    version=version,
                    is_active=active,
                    jd_text="x",
                    requirements_text="x",
                    hard_rule_switches_json={},
                    weights_json={},
                    pass_threshold=60,
                )
            )
        db.commit()

    # 引擎与会话注入：统计/快照都落在测试 SQLite（monkeypatch 自动还原）
    monkeypatch.setattr(service, "_replica_engine", engine)
    monkeypatch.setattr(service, "SessionLocal", factory)
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
    yield SimpleNamespace(engine=engine, factory=factory, cache=fake, app=app, monkeypatch=monkeypatch)
    reset_cache(None)
    engine.dispose()


def _as(client: TestClient, user):
    from app.api.deps import get_current_user

    client.app.dependency_overrides[get_current_user] = lambda: user
    return client


# ---- 口径正确性 ----

def test_metric_definition_per_title_latest_version(env):
    assert service.compute_active_jobs(env.engine) == EXPECTED_ACTIVE


# ---- 越权反例（验收②） ----

def test_personal_role_denied_403(env):
    with pytest.raises(BizError) as ei:
        asyncio.run(service.get_jobs_active(U_PERSONAL))
    assert ei.value.code == 403


def test_personal_role_denied_on_http(env):
    client = _as(TestClient(env.app), U_PERSONAL)
    assert client.get("/api/analytics/metrics/jobs/active").status_code == 403
    assert client.get("/api/analytics/metrics/jobs/active/events").status_code == 403  # SSE 也不进流


# ---- 缓存命中 + 快照落库（验收①） ----

def test_cache_hit_and_snapshot_written(env):
    monkeypatch = env.monkeypatch
    calls = {"n": 0}
    orig_compute = service.compute_active_jobs

    def counting(engine):
        calls["n"] += 1
        return orig_compute(engine)

    monkeypatch.setattr(service, "compute_active_jobs", counting)
    client = _as(TestClient(env.app), U_HR)

    first = client.get("/api/analytics/metrics/jobs/active").json()["data"]
    assert first["value"] == EXPECTED_ACTIVE
    assert first["cache_hit"] is False and first["source"] == "replica"  # 测试注入副本引擎
    assert calls["n"] == 1
    assert first["snapshot_id"] is not None

    second = client.get("/api/analytics/metrics/jobs/active").json()["data"]
    assert second["value"] == EXPECTED_ACTIVE
    assert second["cache_hit"] is True
    assert calls["n"] == 1  # 缓存命中：不再次触库（验收①含缓存命中）

    with env.factory() as db:
        snaps = list(db.execute(select(AnalyticsQuerySnapshot)).scalars())
    assert len(snaps) == 2  # 两次调用各落一行快照（含命中行）
    assert snaps[0].metric_id == "jobs.active" and snaps[0].created_by == 2
    # T-D10 起：快照存 value+group_by+chart+interpretation（T-D11 同快照导出基础）
    assert snaps[0].result_json["value"] == EXPECTED_ACTIVE
    assert snaps[0].result_json["chart"]["chart_type"] == "number"
    assert snaps[0].result_json["interpretation"]["segments"]


def test_sse_stream_events(env):
    client = _as(TestClient(env.app), U_HR)
    with client.stream("GET", "/api/analytics/metrics/jobs/active/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "event: progress" in body and "event: done" in body
    assert '"stage": "started"' in body and '"stage": "computed"' in body
    assert f'"value": {EXPECTED_ACTIVE}' in body


def test_sse_also_writes_snapshot(env):
    client = _as(TestClient(env.app), U_HR)
    with client.stream("GET", "/api/analytics/metrics/jobs/active/events"):
        pass
    with env.factory() as db:
        snaps = list(db.execute(select(AnalyticsQuerySnapshot)).scalars())
    assert len(snaps) == 1  # SSE 端点同样落快照（终态持久化）


# ---- 真实副本一致性（ANALYTICS_DB_URL 已配置时；不可达自动 skip） ----

def test_replica_matches_primary_when_configured():
    from app.config import settings
    from sqlalchemy import create_engine as ce

    if not settings.analytics_db_url:
        pytest.skip("ANALYTICS_DB_URL 未配置")
    try:
        replica = ce(settings.analytics_db_url)
        with replica.connect():
            pass
    except Exception:  # noqa: BLE001
        pytest.skip("分析 PG 副本不可达")
    primary = ce(settings.database_url)
    assert service.compute_active_jobs(replica) == service.compute_active_jobs(primary)
