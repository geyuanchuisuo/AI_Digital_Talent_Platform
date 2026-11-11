"""D23 / T-D-SELF-07：自由问数结果 → 图表规格与一致性。

验收对照（清单 T-D-SELF-07「D16/D23 自由问数图表规格与报告导出补强」）：
- 契约：`build_nl2sql_chart(columns, rows)` 按结果形态确定性推断绘图列，产出与登记指标
  同一规格契约（`chart_render` 可直接消费）；
- 一致性：`chart.data` 逐点等于 `rows`（未截断时 Σseries == Σ 数值列）；
- 截断：序列上限 20，超出置 `truncated_series`；
- 守卫：数值列含非数值（None/bool/字符串）→ `none` + 可读原因，不渲染误导柱高；
  窗口外坏值不影响已绘制点；
- 接入：`POST /api/analytics/nl2sql/query` 成功响应含 `chart`，与 `rows` 一致。

运行：pytest tests/D/test_nl2sql_chart_d23.py -q（纯函数 + SQLite 审计，不依赖真实 PG）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 先行完成全量模型注册
from app.api.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.database import Base
from app.models import Nl2SqlAudit
from app.modules.analytics.charts import _MAX_SERIES, build_nl2sql_chart
from app.modules.analytics.nl2sql import service as nl2sql_service
from app.modules.analytics.nl2sql.whitelist import reset_whitelist_cache
from app.modules.analytics.router import router
from app.modules.reports import chart_render

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
U_HR = SimpleNamespace(id=2, role="hr", person_id=None)


def _rows(n: int, *, value_col: str = "cnt", start: int = 1) -> list[dict]:
    return [{"name": f"项{i}", value_col: start + i} for i in range(n)]


# ---- 契约：形态推断 ----

def test_two_numeric_columns_build_bar_point_by_point():
    rows = _rows(3)
    chart = build_nl2sql_chart(["name", "cnt"], rows)
    assert chart["chart_type"] == "bar"
    assert chart["x_field"] == "name" and chart["y_field"] == "value"
    # 逐点一致：类目与数值都来自同一行
    assert chart["data"] == [{"name": r["name"], "value": float(r["cnt"])} for r in rows]
    assert chart["truncated_series"] is False


def test_bar_values_are_float_and_labels_stringified():
    rows = [{"name": 123, "cnt": 4}]
    chart = build_nl2sql_chart(["name", "cnt"], rows)
    assert chart["data"][0]["name"] == "123" and isinstance(chart["data"][0]["name"], str)
    assert chart["data"][0]["value"] == 4.0 and isinstance(chart["data"][0]["value"], float)


def test_truncation_at_series_cap_and_window_consistency():
    rows = _rows(_MAX_SERIES + 5)
    chart = build_nl2sql_chart(["name", "cnt"], rows)
    assert chart["chart_type"] == "bar"
    assert len(chart["data"]) == _MAX_SERIES
    assert chart["truncated_series"] is True
    # 截断窗口内仍逐点一致
    assert chart["data"] == [{"name": r["name"], "value": float(r["cnt"])}
                             for r in rows[:_MAX_SERIES]]


def test_exactly_cap_rows_not_marked_truncated():
    rows = _rows(_MAX_SERIES)
    chart = build_nl2sql_chart(["name", "cnt"], rows)
    assert len(chart["data"]) == _MAX_SERIES
    assert chart["truncated_series"] is False


def test_sum_of_series_equals_sum_of_value_column():
    rows = _rows(7)
    chart = build_nl2sql_chart(["name", "cnt"], rows)
    assert sum(p["value"] for p in chart["data"]) == float(sum(r["cnt"] for r in rows))


@pytest.mark.parametrize("bad", [None, True, False, "5", [1], {"v": 1}])
def test_non_numeric_value_in_window_returns_none(bad):
    rows = [{"name": "a", "cnt": 1}, {"name": "b", "cnt": bad}, {"name": "c", "cnt": 3}]
    chart = build_nl2sql_chart(["name", "cnt"], rows)
    assert chart["chart_type"] == "none"
    assert "非数值" in chart["reason"]


def test_non_numeric_outside_window_does_not_poison_chart():
    """窗口外（第 21 项起）的坏值不影响已绘制的前 20 点。"""
    rows = _rows(_MAX_SERIES) + [{"name": "坏", "cnt": "不是数字"}]
    chart = build_nl2sql_chart(["name", "cnt"], rows)
    assert chart["chart_type"] == "bar"
    assert len(chart["data"]) == _MAX_SERIES
    assert chart["truncated_series"] is True


def test_single_column_single_row_builds_number_card():
    chart = build_nl2sql_chart(["count(*)"], [{"count(*)": 42}])
    assert chart["chart_type"] == "number"
    assert chart["value"] == 42 and chart["label"] == "count(*)"


def test_single_column_multiple_rows_returns_none():
    """单列多行无法确定类目，如实 none（不猜）。"""
    chart = build_nl2sql_chart(["cnt"], [{"cnt": 1}, {"cnt": 2}])
    assert chart["chart_type"] == "none" and "不支持" in chart["reason"]


def test_three_columns_returns_none():
    chart = build_nl2sql_chart(["a", "b", "c"], [{"a": 1, "b": 2, "c": 3}])
    assert chart["chart_type"] == "none" and "不支持" in chart["reason"]


def test_empty_rows_returns_none():
    assert build_nl2sql_chart(["a", "b"], [])["chart_type"] == "none"
    assert build_nl2sql_chart(["a", "b"], None)["chart_type"] == "none"


def test_row_missing_value_column_returns_none():
    chart = build_nl2sql_chart(["name", "cnt"], [{"name": "a"}])
    assert chart["chart_type"] == "none" and "非数值" in chart["reason"]


def test_label_column_missing_still_renders_dash():
    """类目缺失不致命：以 "-" 占位（与登记指标同口径）。"""
    chart = build_nl2sql_chart(["name", "cnt"], [{"cnt": 1}])
    assert chart["chart_type"] == "bar"
    assert chart["data"][0]["name"] == "-"


# ---- 与报告渲染器同契约 ----

def test_bar_spec_consumable_by_chart_render():
    chart = build_nl2sql_chart(["name", "cnt"], _rows(5))
    png = chart_render.render_chart_png(chart, title="自由问数")
    assert png is not None and png.startswith(PNG_MAGIC)


def test_number_spec_consumable_by_chart_render():
    chart = build_nl2sql_chart(["count(*)"], [{"count(*)": 7}])
    png = chart_render.render_chart_png(chart, title="自由问数")
    assert png is not None and png.startswith(PNG_MAGIC)


def test_none_spec_not_rendered():
    assert chart_render.render_chart_png(
        build_nl2sql_chart(["a", "b", "c"], [{"a": 1, "b": 2, "c": 3}]), title="t") is None


# ---- 接入：nl2sql_query 响应含 chart ----

class _FakeGenerator:
    def __init__(self) -> None:
        self.next_sql: str | None = None

    def set(self, sql: str | None) -> None:
        self.next_sql = sql

    def generate_sql(self, question: str, schema_context: str) -> str | None:
        return self.next_sql


@pytest.fixture()
def env(monkeypatch):
    """SQLite 审计 + Fake 生成器 + 静态白名单；执行器由用例 monkeypatch。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Nl2SqlAudit.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    fake_gen = _FakeGenerator()
    fake_gen.set("SELECT name, cnt FROM job_profiles")
    monkeypatch.setattr(nl2sql_service, "_resolve_engine", lambda: (None, "fake"))
    monkeypatch.setattr(nl2sql_service, "get_generator", lambda: fake_gen)
    monkeypatch.setattr(nl2sql_service, "SessionLocal", factory)
    monkeypatch.setattr(nl2sql_service, "settings_nl2sql_timeout", lambda: 3)
    monkeypatch.setattr(nl2sql_service, "settings_nl2sql_max_rows", lambda: 1000)
    monkeypatch.setattr(
        nl2sql_service, "get_whitelist",
        lambda _engine: {"job_profiles": frozenset({"id"})},
    )
    reset_whitelist_cache()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    client = TestClient(app)
    client.app.dependency_overrides[get_current_user] = lambda: U_HR
    yield SimpleNamespace(factory=factory, fake=fake_gen, client=client)
    reset_whitelist_cache()
    engine.dispose()


def _stub_execute(monkeypatch, columns: list[str], rows: list[dict]):
    def _exec(*a, **kw):
        return {"columns": columns, "rows": rows,
                "row_count": len(rows), "truncated": False}
    monkeypatch.setattr(nl2sql_service, "execute_readonly", _exec)


def test_nl2sql_query_response_chart_matches_rows(env, monkeypatch):
    rows = [{"department": "技术部", "active_jobs": 4},
            {"department": "产品部", "active_jobs": 2}]
    _stub_execute(monkeypatch, ["department", "active_jobs"], rows)

    resp = env.client.post("/api/analytics/nl2sql/query", json={"question": "各部门活跃岗位"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["chart"]["chart_type"] == "bar"
    assert data["chart"]["data"] == [
        {"name": "技术部", "value": 4.0}, {"name": "产品部", "value": 2.0}]
    # 与 rows 逐点一致（同一事实源）
    assert [p["value"] for p in data["chart"]["data"]] == \
        [float(r["active_jobs"]) for r in rows]


def test_nl2sql_query_response_chart_none_for_unplottable(env, monkeypatch):
    rows = [{"a": 1, "b": 2, "c": 3}]
    _stub_execute(monkeypatch, ["a", "b", "c"], rows)

    resp = env.client.post("/api/analytics/nl2sql/query", json={"question": "q"})
    assert resp.status_code == 200, resp.text
    chart = resp.json()["data"]["chart"]
    assert chart["chart_type"] == "none" and chart["reason"]


def test_nl2sql_query_chart_truncated_flag_matches_rows(env, monkeypatch):
    rows = [{"name": f"项{i}", "cnt": i + 1} for i in range(_MAX_SERIES + 2)]
    _stub_execute(monkeypatch, ["name", "cnt"], rows)

    resp = env.client.post("/api/analytics/nl2sql/query", json={"question": "q"})
    assert resp.status_code == 200, resp.text
    chart = resp.json()["data"]["chart"]
    assert chart["truncated_series"] is True and len(chart["data"]) == _MAX_SERIES
