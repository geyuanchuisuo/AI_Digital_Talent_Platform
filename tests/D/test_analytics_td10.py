"""T-D10 多维分析、自动出图与决策建议用例。

验收：①同一问题多维组合结果可复现 ②解读文本机器可区分事实段/推断段（且无因果措辞）
③图表数据与表格一致（构造保证 + 逐点核对）。
运行：pytest tests/D/test_analytics_td10.py -v（SQLite 内存库）。
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
    MatchBatch,
    MatchResult,
    MetricRegistry,
    ProjectDemand,
    ProjectGroup,
)
from app.modules.analytics import charts, insight, service
from app.modules.analytics.insight import has_causal_language
from app.modules.analytics.models import seed_metric_registry
from app.modules.analytics.router import router

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=1)

CORE_TABLES = (
    JobProfile.__table__,
    ProjectDemand.__table__,
    Application.__table__,
    ProjectGroup.__table__,
    MatchBatch.__table__,
    MatchResult.__table__,
    MetricRegistry.__table__,
    AnalyticsQuerySnapshot.__table__,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=CORE_TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
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


def _make_job(db, title, version, active, dept=None, threshold="60.00"):
    row = JobProfile(title=title, version=version, is_active=active, department=dept,
                     jd_text="x", requirements_text="x", hard_rule_switches_json={},
                     weights_json={}, pass_threshold=threshold)
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 多维分组 + 验收①：多维组合可复现
# --------------------------------------------------------------------------- #

def test_active_jobs_by_department_reproducible(env):
    """同一问题多维组合可复现：同参数两次调用（缓存 miss→hit）结果完全一致（验收①）。"""
    with env.factory() as db:
        _make_job(db, "后端工程师", 1, 1, dept="技术部")
        _make_job(db, "算法工程师", 1, 1, dept="技术部")
        _make_job(db, "产品经理", 1, 1, dept="产品部")
        _make_job(db, "测试工程师", 1, 1, dept=None)
        _make_job(db, "销售工程师", 1, 0, dept="销售部")  # inactive → 不计
        db.commit()

    r1 = _run(service.query_metric(U_HR, "jobs.active", group_by="department"))
    assert r1.value == 4 and r1.table["columns"] == ["department", "active_jobs"]
    rows = {r["department"]: r["active_jobs"] for r in r1.table["rows"]}
    assert rows == {"技术部": 2, "产品部": 1, "未指定": 1}
    r2 = _run(service.query_metric(U_HR, "jobs.active", group_by="department"))
    assert r2.cache_hit is True
    # 复现：两次结果逐字段一致
    assert r2.value == r1.value and r2.table == r1.table and r2.chart == r1.chart
    assert r2.interpretation == r1.interpretation
    # 快照逐组合留痕
    with env.factory() as db:
        snaps = db.execute(select(AnalyticsQuerySnapshot)).scalars().all()
    assert len(snaps) == 2


def test_demand_gap_group_by_project_group(env):
    with env.factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        db.add_all([
            ProjectGroup(id=1001, name="A 项目群", status="active"),
            ProjectGroup(id=1002, name="B 项目群", status="active"),
        ])
        d1 = ProjectDemand(position_name="需求A-5人", headcount=5, status="open",
                           job_profile_id=job.id, project_group_id=1001)
        d2 = ProjectDemand(position_name="需求B-3人", headcount=3, status="in_progress",
                           job_profile_id=job.id, project_group_id=1002)
        db.add_all([d1, d2])
        db.flush()
        db.add_all([
            Application(talent_profile_id=11, job_profile_id=job.id,
                        internal_stage="offer_accepted", result_confirmed_by=2,
                        external_stage="closed", result_category="hired"),
            Application(talent_profile_id=12, job_profile_id=job.id,
                        internal_stage="offer_accepted", result_confirmed_by=2,
                        external_stage="closed", result_category="hired"),
        ])
        db.commit()

    r = _run(service.query_metric(U_HR, "job.demand_progress_gap", group_by="project_group"))
    assert r.value == 4  # A 组 3 + B 组 1
    rows = {r_["project_group"]: r_["gap"] for r_ in r.table["rows"]}
    assert rows == {"A 项目群": 3, "B 项目群": 1}
    # 组合复现：group_by + 参数过滤 同轴
    combo = _run(service.query_metric(U_HR, "job.demand_progress_gap",
                                      params={"project_group_id": 1001},
                                      group_by="project_group", bypass_cache=True))
    assert combo.value == 3 and len(combo.table["rows"]) == 1


def test_group_by_validation_422(env):
    with pytest.raises(BizError) as ei:
        _run(service.query_metric(U_HR, "job.demand_progress_gap", group_by="bogus_dim"))
    assert ei.value.code == 422 and "分组维度" in ei.value.message
    # 待口径指标：awaiting 检查先于维度白名单（准入链顺序）
    with pytest.raises(BizError) as ei2:
        _run(service.query_metric(U_HR, "parse.success_failure", group_by="department"))
    assert ei2.value.code == 422 and "待交付" in ei2.value.message


def test_group_by_http_endpoint(env):
    with env.factory() as db:
        _make_job(db, "后端工程师", 1, 1, dept="技术部")
        db.commit()
    resp = env.client.get("/api/analytics/metrics/jobs/active?group_by=department")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["group_by"] == "department" and data["chart"]["chart_type"] == "bar"
    resp2 = env.client.get("/api/analytics/metrics/jobs/active?group_by=bogus")
    assert resp2.status_code == 422 and "分组维度" in resp2.json()["message"]


def test_403_precedes_breakdown(env, monkeypatch):
    def _boom(engine, params, *, group_by=None):  # pragma: no cover
        raise AssertionError("403 之后不得触达 breakdown")

    sentinel = {k: dict(v) for k, v in service.BREAKDOWN_FUNCS.items()}
    for k in sentinel:
        sentinel[k] = {"department": _boom, "project_group": _boom, "job": _boom}
    monkeypatch.setattr(service, "BREAKDOWN_FUNCS", sentinel)
    with pytest.raises(BizError) as ei:
        _run(service.query_metric(U_PERSONAL, "job.demand_progress_gap", group_by="project_group"))
    assert ei.value.code == 403


def test_cache_key_distinguishes_group_by(env):
    with env.factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        db.add(ProjectDemand(position_name="需求A", headcount=5, status="open",
                             job_profile_id=job.id, project_group_id=1001))
        db.commit()
    grouped = _run(service.query_metric(U_HR, "job.demand_progress_gap", group_by="project_group"))
    plain = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    # 同指标同参数、不同 group_by → 不同缓存键，各自正确
    assert grouped.value == plain.value == 5  # 单需求无已录用：缺口=5


# --------------------------------------------------------------------------- #
# 自动出图：验收③图表数据与表格一致
# --------------------------------------------------------------------------- #

def test_chart_data_matches_table_gap(env):
    with env.factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        db.add_all([
            ProjectDemand(position_name="需求A", headcount=5, status="open",
                          job_profile_id=job.id, project_group_id=1001),
            ProjectDemand(position_name="需求B", headcount=3, status="open",
                          job_profile_id=job.id, project_group_id=1002),
        ])
        db.commit()
    r = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    chart = r.chart
    assert chart["chart_type"] == "bar"
    # 逐点核对：图表序列 == 表格行（验收③）
    table_pairs = {(row["position_name"], row["gap"]) for row in r.table["rows"]}
    chart_pairs = {(p["name"], p["value"]) for p in chart["data"]}
    assert chart_pairs == table_pairs
    # 合计一致：序列值之和 == 指标值
    assert sum(p["value"] for p in chart["data"]) == r.value


def test_chart_number_for_scalar_jobs_active(env):
    with env.factory() as db:
        _make_job(db, "后端工程师", 1, 1, dept="技术部")
        db.commit()
    r = _run(service.query_metric(U_HR, "jobs.active"))
    assert r.chart == {"chart_type": "number", "label": "活跃岗位数", "value": 1}


def test_chart_grouped_bar(env):
    with env.factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        db.add(ProjectDemand(position_name="需求A", headcount=5, status="open",
                             job_profile_id=job.id, project_group_id=1001))
        db.add(ProjectDemand(position_name="需求B", headcount=3, status="open",
                             job_profile_id=job.id, project_group_id=1002))
        db.commit()
    r = _run(service.query_metric(U_HR, "job.demand_progress_gap", group_by="project_group"))
    assert r.chart["chart_type"] == "bar"
    assert {(p["name"], p["value"]) for p in r.chart["data"]} == \
        {(row["project_group"], row["gap"]) for row in r.table["rows"]}


def test_chart_none_for_no_data(env):
    with env.factory() as db:
        _make_job(db, "空岗位", 1, 1, threshold="60.00")
        db.commit()
    r = _run(service.query_metric(U_HR, "match.threshold_reference"))
    assert r.note == "暂无数据" and r.chart["chart_type"] == "none"


def test_chart_series_capped_at_20(env):
    with env.factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        for i in range(25):
            db.add(ProjectDemand(position_name=f"需求{i:02d}", headcount=1, status="open",
                                 job_profile_id=job.id, project_group_id=1000 + i))
        db.commit()
    r = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    assert r.chart["truncated_series"] is True
    assert len(r.chart["data"]) == 20 and len(r.table["rows"]) == 25


# --------------------------------------------------------------------------- #
# 决策建议：验收②事实/推断机器可区分 + 禁因果措辞
# --------------------------------------------------------------------------- #

def test_interpretation_segments_machine_distinguishable(env):
    with env.factory() as db:
        job = _make_job(db, "后端工程师", 1, 1)
        db.add(ProjectDemand(position_name="需求A", headcount=5, status="open",
                             job_profile_id=job.id, project_group_id=1001))
        db.commit()
    r = _run(service.query_metric(U_HR, "job.demand_progress_gap"))
    interp = r.interpretation
    assert interp["generator"] == "rule"
    assert len(interp["segments"]) >= 2
    for seg in interp["segments"]:
        assert seg["kind"] in ("fact", "inference")  # 机器可判别
        assert seg["text"]
        if seg["kind"] == "inference":
            assert seg.get("basis")  # 推断段显式引用依据
        if seg["kind"] == "fact":
            assert "ref" in seg  # 事实段携带数据引用


def test_no_causal_language_in_any_interpretation(env):
    """「不把相关说成因果」：全量指标解读扫描因果措辞——零命中。"""
    with env.factory() as db:
        job = _make_job(db, "达线岗", 1, 1, threshold="60.00")
        db.add(job)
        db.add(ProjectDemand(position_name="需求A", headcount=5, status="open",
                             job_profile_id=job.id, project_group_id=1001))
        db.commit()
    for metric_id, kwargs in [
        ("jobs.active", {}),
        ("jobs.active", {"group_by": "department"}),
        ("job.demand_progress_gap", {}),
        ("job.demand_progress_gap", {"group_by": "project_group"}),
    ]:
        r = _run(service.query_metric(U_HR, metric_id, bypass_cache=True, **kwargs))
        assert not has_causal_language(r.interpretation["segments"]), \
            f"{metric_id}: 解读文本含因果措辞"


def test_interpretation_deterministic_and_references_real_data(env):
    with env.factory() as db:
        job = _make_job(db, "达线岗", 1, 1, threshold="60.00")
        db.add(ProjectDemand(position_name="缺口王", headcount=5, status="open",
                             job_profile_id=job.id, project_group_id=1001))
        db.commit()
    r1 = _run(service.query_metric(U_HR, "job.demand_progress_gap"))
    r2 = _run(service.query_metric(U_HR, "job.demand_progress_gap", bypass_cache=True))
    assert r1.interpretation == r2.interpretation  # 完全可复现
    texts = " ".join(s["text"] for s in r1.interpretation["segments"])
    assert "缺口王" in texts and "5" in texts  # 事实段引用真实数据


# --------------------------------------------------------------------------- #
# 全局守卫：因果措辞扫描函数本身
# --------------------------------------------------------------------------- #

def test_has_causal_language_detects():
    segs = [{"kind": "inference", "text": "A 增加 导致 B 下降"}]
    assert insight.has_causal_language(segs) is True
    segs2 = [{"kind": "inference", "text": "A 与 B 同时上升，建议关注"}]
    assert insight.has_causal_language(segs2) is False
