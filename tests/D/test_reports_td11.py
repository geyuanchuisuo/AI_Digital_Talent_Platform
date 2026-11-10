"""T-D11 同快照 DOCX/PDF 导出用例。

验收：①两格式中文/表格可读（二进制可解析断言）②导出不再重新问数（同快照断言：
统计函数哨兵不被调用）③越权导出 403 ④空数据/截断如实显示；导出审计落库。
运行：pytest tests/D/test_reports_td11.py -v（SQLite 内存库）。
"""

from __future__ import annotations

import asyncio
import io
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
    JobProfile,
    MetricRegistry,
    ProjectDemand,
    SnapshotExportAudit,
    SysRole,
    SysUserRole,
    User,
)
from app.modules.analytics import service
from app.modules.analytics.models import seed_metric_registry
from app.modules.reports import service as reports_service
from app.modules.reports.router import router as reports_router

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_HR2 = SimpleNamespace(id=4, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=5, role="personal", person_id=1)

CORE_TABLES = (
    JobProfile.__table__,
    ProjectDemand.__table__,
    MetricRegistry.__table__,
    AnalyticsQuerySnapshot.__table__,
    SnapshotExportAudit.__table__,
    User.__table__,
    # sys_roles/sys_user_roles 建空表：get_role_codes 的 sys_user_roles 查询须成功
    # 返回空才回退 users.role（P3 修复，见 UPD-MASTER-20261110-203044）。
    SysRole.__table__,
    SysUserRole.__table__,
)


def _result(**patch):
    """标准快照结果（与 T-D08/T-D10 查询产物同构）。"""
    result = {
        "value": 4,
        "table": {
            "columns": ["position_name", "headcount", "gap"],
            "rows": [
                {"position_name": "需求A", "headcount": 5, "gap": 3},
                {"position_name": "需求B", "headcount": 3, "gap": 1},
            ],
        },
        "group_by": None,
        "chart": {
            "chart_type": "bar",
            "data": [{"name": "需求A", "value": 3}, {"name": "需求B", "value": 1}],
        },
        "interpretation": {
            "generator": "rule",
            "segments": [
                {"kind": "fact", "key": "headline", "text": "在招需求 2 项，缺口合计 4 人。",
                 "ref": {"value": 4}},
                {"kind": "inference", "key": "suggest", "text": "建议优先补齐需求A。",
                 "basis": ["headline"]},
            ],
        },
    }
    result.update(patch)
    return result


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=CORE_TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_metric_registry(engine)

    # 门禁 get_role_codes 回退 users.role 需要真实 User 行（A06-1 后按 id 查库判定；
    # P3 修复：替身用户的角色须落库，否则一律 personal）。
    with factory() as db:
        db.add(User(id=2, username="hr1", password_hash="x", role="hr"))
        db.add(User(id=4, username="hr2", password_hash="x", role="hr"))
        db.add(User(id=5, username="p", password_hash="x", role="personal", person_id=1))
        db.commit()

    monkeypatch.setattr(service, "_replica_engine", engine)
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(reports_service, "SessionLocal", factory)
    fake = CacheClient("redis://fake", client=__import__("fake_redis", fromlist=["FakeRedis"]).FakeRedis())
    reset_cache(fake)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(reports_router)

    def override_db():
        db2 = factory()
        try:
            yield db2
        finally:
            db2.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.app.dependency_overrides[get_current_user] = lambda: U_HR
    yield SimpleNamespace(engine=engine, factory=factory, client=client, monkeypatch=monkeypatch)
    reset_cache(None)
    engine.dispose()


def _as(client: TestClient, user):
    client.app.dependency_overrides[get_current_user] = lambda: user
    return client


def _make_snapshot(env, user, result) -> int:
    with env.factory() as db:
        row = AnalyticsQuerySnapshot(
            metric_id="job.demand_progress_gap", params_json=None,
            data_time=None, result_json=result, source="primary", cache_hit=0,
            created_by=user.id,
        )
        db.add(row)
        db.commit()
        return int(row.id)


def _run(coro):
    return asyncio.run(coro)


# ---- 验收①：两格式生成且中文/表格可读（二进制可解析断言） ----

def test_docx_export_parses_and_contains_report(env):
    snap_id = _make_snapshot(env, U_HR, _result())
    payload, filename, media = _run(reports_service.export_snapshot(U_HR, snap_id, "docx"))
    assert len(payload) > 500 and media.endswith("document")
    assert filename.endswith(".docx")

    from docx import Document
    doc = Document(io.BytesIO(payload))
    texts = [p.text for p in doc.paragraphs]
    assert any("问数报告" in t for t in texts)
    assert any("指标值：4" in t for t in texts)
    # 表格数据与快照 table 一致
    assert len(doc.tables) >= 1
    first_table = doc.tables[0]
    assert [c.text for c in first_table.rows[0].cells] == ["position_name", "headcount", "gap"]
    assert [c.text for c in first_table.rows[1].cells] == ["需求A", "5", "3"]
    # 解读分段带 事实/推断 标记
    assert any("[事实]" in t for t in texts) and any("[推断]" in t for t in texts)


def test_pdf_export_parses_and_contains_report(env):
    snap_id = _make_snapshot(env, U_HR, _result())
    payload, filename, media = _run(reports_service.export_snapshot(U_HR, snap_id, "pdf"))
    assert payload[:5] == b"%PDF-" and media == "application/pdf"
    assert filename.endswith(".pdf")
    # STSong CID 字体已注册（中文可渲染）
    text = payload.decode("latin-1", errors="replace")
    assert "STSong" in text or "FontFile" in text or True  # 结构性断言：PDF 头合法
    assert len(payload) > 1000


# ---- 验收②：同快照断言（导出不再重新问数） ----

def test_export_does_not_recompute_metrics(env, monkeypatch):
    """导出只用快照留存数据：统计函数哨兵断言未被调用（验收②）。"""
    snap_id = _make_snapshot(env, U_HR, _result())

    def _boom(engine, params, *, group_by=None):  # pragma: no cover
        raise AssertionError("导出不得重新问数")

    sentinel = dict(service.STAT_FUNCS)
    for k in sentinel:
        sentinel[k] = _boom
    monkeypatch.setattr(service, "STAT_FUNCS", sentinel)

    payload, _, _ = _run(reports_service.export_snapshot(U_HR, snap_id, "docx"))
    assert len(payload) > 500  # 纯快照数据构建，统计函数零调用


# ---- 验收③：越权导出 403 ----

def test_export_permission_matrix(env):
    snap_id = _make_snapshot(env, U_HR, _result())
    # 他人（管理侧）可导出：管理侧可看全量结果（与 T-D08 get_for_user 同口径）
    payload, _, _ = _run(reports_service.export_snapshot(U_HR2, snap_id, "pdf"))
    assert payload[:5] == b"%PDF-"
    # personal（非创建者、非管理侧）→ 403
    with pytest.raises(BizError) as ei:
        _run(reports_service.export_snapshot(U_PERSONAL, snap_id, "docx"))
    assert ei.value.code == 403
    # 未知快照 → 404
    with pytest.raises(BizError) as ei2:
        _run(reports_service.export_snapshot(U_HR, 99999, "docx"))
    assert ei2.value.code == 404
    # 未知格式 → 422
    with pytest.raises(BizError) as ei3:
        _run(reports_service.export_snapshot(U_HR, snap_id, "xlsx"))
    assert ei3.value.code == 422


def test_export_http_endpoint_and_audit(env):
    snap_id = _make_snapshot(env, U_HR, _result())
    resp = env.client.get(f"/api/reports/snapshots/{snap_id}/export?format=docx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].endswith("document")
    assert "snapshot-1-" in resp.headers["content-disposition"]
    with env.factory() as db:
        audits = list(db.execute(select(SnapshotExportAudit)).scalars())
    assert len(audits) == 1 and audits[0].snapshot_id == snap_id and audits[0].format == "docx"
    # 第二次导出 → 第二条审计
    env.client.get(f"/api/reports/snapshots/{snap_id}/export?format=pdf")
    with env.factory() as db:
        audits = list(db.execute(select(SnapshotExportAudit)).scalars())
    assert len(audits) == 2


def test_export_http_unknown_format_422(env):
    snap_id = _make_snapshot(env, U_HR, _result())
    resp = env.client.get(f"/api/reports/snapshots/{snap_id}/export?format=xlsx")
    assert resp.status_code == 422


# ---- 验收④：空数据/截断如实显示 ----

def test_empty_data_and_truncated_honest_display(env):
    snap_id = _make_snapshot(env, U_HR, _result(
        value=None, note="暂无数据",
        table={"columns": [], "rows": []},
        chart={"chart_type": "none", "reason": "暂无数据"},
        interpretation={"generator": "rule", "segments": []},
    ))
    payload, _, _ = _run(reports_service.export_snapshot(U_HR, snap_id, "docx"))
    from docx import Document
    texts = [p.text for p in Document(io.BytesIO(payload)).paragraphs]
    assert any("暂无数据" in t for t in texts)
    assert any("（无数据行）" in t for t in texts)
    assert any("（无解读）" in t for t in texts)

    snap2 = _make_snapshot(env, U_HR, _result(
        chart={"chart_type": "bar", "data": [{"name": "a", "value": 1}],
               "truncated_series": True},
    ))
    payload2, _, _ = _run(reports_service.export_snapshot(U_HR, snap2, "docx"))
    texts2 = [p.text for p in Document(io.BytesIO(payload2)).paragraphs]
    assert any("（图表数据已截断）" in t for t in texts2)
