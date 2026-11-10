"""T-D08 白名单指标查询服务（第一层）用例。

验收：①可查指标与人工 SQL 一致（真实 MySQL 集成用例在文件尾）②未登记/待口径拒绝
③零分母「暂无数据」④先过滤后聚合（403 先于计算；参数过滤先于聚合）。
运行：pytest tests/D/test_analytics_td08.py -v（SQLite 内存库 + 真实 MySQL 集成）。
"""

from __future__ import annotations

import asyncio
import socket
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
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
    MatchBatch,
    MatchResult,
    MetricRegistry,
    ProjectDemand,
)
from app.modules.analytics import service
from app.modules.analytics.models import seed_metric_registry
from app.modules.analytics.router import router

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=1)

CORE_TABLES = (
    JobProfile.__table__,
    ProjectDemand.__table__,
    Application.__table__,
    MatchBatch.__table__,
    MatchResult.__table__,
    MetricRegistry.__table__,
    AnalyticsQuerySnapshot.__table__,
)


def _mysql_up() -> bool:
    try:
        socket.create_connection(("localhost", 3307), timeout=0.5)
        return True
    except OSError:
        return False


def _make_job(db, title: str, version: int, active: int, threshold: str = "60.00") -> JobProfile:
    row = JobProfile(
        title=title,
        version=version,
        is_active=active,
        jd_text="x",
        requirements_text="x",
        hard_rule_switches_json={},
        weights_json={},
        pass_threshold=Decimal(threshold),
    )
    db.add(row)
    db.flush()
    return row


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


def _run(coro):
    return asyncio.run(coro)


# ---- 验收②：registry 准入 ----

def test_unknown_metric_rejected_422(env):
    with pytest.raises(BizError) as ei:
        _run(service.query_metric(U_HR, "nope.metric"))
    assert ei.value.code == 422 and "未登记" in ei.value.message


def test_awaiting_caliber_metric_rejected_422(env):
    """B/C 域卡片落库为 awaiting_caliber：查询拒绝且文案说明口径待交付。"""
    with pytest.raises(BizError) as ei:
        _run(service.query_metric(U_HR, "parse.success_failure"))
    assert ei.value.code == 422 and "待交付" in ei.value.message


def test_registry_seeded_seven_rows(env):
    with env.factory() as db:
        rows = list(db.execute(select(MetricRegistry)).scalars())
    assert len(rows) == 7  # 六卡 + 切片指标
    statuses = {r.metric_id: r.status for r in rows}
    assert statuses["job.demand_progress_gap"] == "active"
    assert statuses["match.threshold_reference"] == "active"
    assert statuses["parse.success_failure"] == "awaiting_caliber"
    assert statuses["exam.pass_rate"] == "awaiting_caliber"
    assert statuses["training.formal_completion_rate"] == "awaiting_caliber"
    assert statuses["learning.self_reported_progress"] == "awaiting_caliber"


def test_registry_endpoint_lists_catalog(env):
    resp = env.client.get("/api/analytics/metrics")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert {i["metric_id"] for i in items} >= {
        "jobs.active", "job.demand_progress_gap", "match.threshold_reference",
        "parse.success_failure", "exam.pass_rate",
    }


# ---- 验收④：403 先于任何计算 ----

def test_403_precedes_computation(env, monkeypatch):
    def _boom(engine, params):  # pragma: no cover - 若被调用测试即失败
        raise AssertionError("403 之后不得触达统计函数")

    sentinel = dict(service.STAT_FUNCS)
    sentinel["job.demand_progress_gap"] = _boom
    monkeypatch.setattr(service, "STAT_FUNCS", sentinel)
    with pytest.raises(BizError) as ei:
        _run(service.query_metric(U_PERSONAL, "job.demand_progress_gap"))
    assert ei.value.code == 403


def test_personal_denied_on_http(env):
    env.client.app.dependency_overrides[get_current_user] = lambda: U_PERSONAL
    assert env.client.get("/api/analytics/metrics").status_code == 403
    assert env.client.get("/api/analytics/metrics/job.demand_progress_gap").status_code == 403


# ---- 验收①：公式与人工 SQL 一致（SQLite 先验） ----

def _seed_gap_data(factory):
    """同岗两需求：d1 headcount5（缺口3）、d2 headcount3（缺口1，同岗已录用池共享）→ 总 4。"""
    with factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        d1 = ProjectDemand(position_name="需求A-5人", headcount=5, status="open",
                           job_profile_id=job.id, project_group_id=1001)
        d2 = ProjectDemand(position_name="需求B-3人", headcount=3, status="in_progress",
                           job_profile_id=job.id, project_group_id=1002)
        db.add_all([d1, d2])
        db.flush()
        # 已录用 2 人（offer_accepted + HR 确认）——同 job_profile 共享池
        db.add_all([
            Application(talent_profile_id=11, job_profile_id=job.id,
                        internal_stage="offer_accepted", result_confirmed_by=2,
                        external_stage="closed", result_category="hired"),
            Application(talent_profile_id=12, job_profile_id=job.id,
                        internal_stage="offer_accepted", result_confirmed_by=2,
                        external_stage="closed", result_category="hired"),
        ])
        db.commit()
        return {"job_id": job.id, "d1": d1.id, "d2": d2.id}


def test_demand_gap_matches_manual_formula(env):
    ids = _seed_gap_data(env.factory)
    result = _run(service.query_metric(U_HR, "job.demand_progress_gap"))
    assert result.value == 4  # d1: 5−2=3，d2: 3−2=1（同岗已录用池共享，逐需求非负）
    by_id = {r["demand_id"]: r for r in result.table["rows"]}
    assert by_id[ids["d1"]]["gap"] == 3
    assert by_id[ids["d2"]]["gap"] == 1
    # 已录用数只认 HR 确认口径：把 result_confirmed_by 置空 → 已录用 0 → 总缺口 8
    with env.factory() as db:
        for a in db.execute(select(Application)).scalars():
            a.result_confirmed_by = None
        db.commit()
    after = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    assert after.value == 8  # 5+3，人工推进不混入（§12.3）


def test_param_filter_before_aggregate(env):
    _seed_gap_data(env.factory)
    filtered = _run(service.query_metric(U_HR, "job.demand_progress_gap",
                                         params={"project_group_id": 1001}))
    assert filtered.value == 3  # 只算 A 组（过滤先于聚合）
    unfiltered = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    assert unfiltered.value == 4


def test_gap_metric_uses_cache(env):
    _seed_gap_data(env.factory)
    first = _run(service.query_metric(U_HR, "job.demand_progress_gap"))
    assert first.cache_hit is False
    second = _run(service.query_metric(U_HR, "job.demand_progress_gap"))
    assert second.cache_hit is True and second.value == first.value


# ---- 验收③：零分母「暂无数据」 ----

def test_threshold_reference_zero_denominator(env):
    """无 match_results → 有效分母 0 → value=None + 暂无数据（验收③）。"""
    with env.factory() as db:
        _make_job(db, "空岗位", 1, 1)
        db.commit()
    result = _run(service.query_metric(U_HR, "match.threshold_reference"))
    assert result.value is None
    assert result.note == "暂无数据"


def test_threshold_reference_matches_manual_sql(env):
    with env.factory() as db:
        job = _make_job(db, "达线岗", 1, 1, threshold="60.00")
        mb = MatchBatch(job_profile_id=job.id, job_profile_version=1,
                        config_snapshot_json={}, talent_scope_json={}, status="done",
                        total=3, created_by=2)
        db.add(mb)
        db.flush()
        # 达线 2（70/80），硬淘汰 1（score=0 排除分母），未达线 1（50）→ 2/3
        db.add_all([
            MatchResult(match_batch_id=mb.id, talent_profile_id=1, score=Decimal("70")),
            MatchResult(match_batch_id=mb.id, talent_profile_id=2, score=Decimal("80")),
            MatchResult(match_batch_id=mb.id, talent_profile_id=3, score=Decimal("50")),
            MatchResult(match_batch_id=mb.id, talent_profile_id=4, score=Decimal("0"), hard_fail=1),
        ])
        db.commit()
    result = _run(service.query_metric(U_HR, "match.threshold_reference", bypass_cache=True))
    assert result.value == pytest.approx(2 / 3, abs=1e-4)
    rows = result.table["rows"]
    assert len(rows) == 1 and rows[0]["total"] == 3 and rows[0]["reached"] == 2


# ---- 验收①（真实 MySQL 集成）：registry 落库 + 缺口公式人工核算 ----

@pytest.mark.skipif(not _mysql_up(), reason="业务 MySQL(3307) 未运行")
def test_real_mysql_registry_and_gap_consistency(monkeypatch):
    from app.config import settings
    from sqlalchemy import create_engine

    # 数据插在主库：强制走主库路径（副本无测试行，且 ETL 有延迟）
    monkeypatch.setattr(service, "_replica_engine", None)
    monkeypatch.setattr(settings, "analytics_db_url", "")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM metric_registry")).scalar_one()
    assert n == 7  # D_0004 种子已在真实库生效

    marker = "T-D08-一致性"
    from sqlalchemy import insert

    with engine.begin() as db:
        job_id = int(db.execute(
            insert(JobProfile).values(title=marker, version=999, is_active=1, jd_text="x",
                                      requirements_text="x", hard_rule_switches_json={},
                                      weights_json={}, pass_threshold=Decimal("60.00"))
        ).inserted_primary_key[0])
        demand_id = int(db.execute(
            insert(ProjectDemand).values(position_name=marker, headcount=5, status="open",
                                         job_profile_id=job_id, project_group_id=999001)
        ).inserted_primary_key[0])
        db.execute(insert(Application).values(talent_profile_id=990001, job_profile_id=job_id,
                                              internal_stage="offer_accepted", result_confirmed_by=2,
                                              external_stage="closed", result_category="hired"))
    try:
        # 人工 SQL（同公式独立表达，MySQL GREATEST 版）
        with engine.connect() as conn:
            manual = conn.execute(text(
                "SELECT GREATEST(headcount - (SELECT COUNT(DISTINCT a.talent_profile_id) "
                "FROM applications a WHERE a.job_profile_id = :jid "
                "AND a.internal_stage='offer_accepted' AND a.result_confirmed_by IS NOT NULL), 0) "
                "FROM project_demands WHERE id = :did"
            ), {"jid": job_id, "did": demand_id}).scalar_one()
        r = _run(service.query_metric(U_HR, "job.demand_progress_gap",
                                      params={"project_group_id": 999001}, bypass_cache=True))
        assert r.value == manual == 4  # 5 − 1（HR 确认已录用 1）
        assert any(row["demand_id"] == demand_id for row in r.table["rows"])
    finally:
        with engine.begin() as db:
            db.execute(text("DELETE FROM applications WHERE job_profile_id = :j AND talent_profile_id=990001"),
                       {"j": job_id})
            db.execute(text("DELETE FROM project_demands WHERE id = :d"), {"d": demand_id})
            db.execute(text("DELETE FROM job_profiles WHERE id = :j"), {"j": job_id})
