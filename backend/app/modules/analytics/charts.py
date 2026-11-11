"""图表规格生成（T-D10 自动出图）。

图表数据**只从同一响应的 table 派生**（验收③：图表数据与表格一致——由构造保证，
另有测试逐点核对）。图表类型按数据形态确定性选择（不依赖 LLM）：

- 表无行且 value 非空 → `number`（大数字卡）
- 表有行且配置了 label/value 列 → `bar`（类目幅度对比；序列截断至 20 项防长尾）
- 暂无数据 / 无可绘图列 / 数值列含非数值 → `none`（前端不渲染图区，避免误导图形）
"""

from __future__ import annotations

from typing import Any

#: metric_id → table 的绘图列（label=类目列, value=数值列；均须出现在 table.columns）。
#: 无分组查询与分组查询的表结构不同，分别声明。
CHART_COLUMNS: dict[tuple[str, str | None], tuple[str, str]] = {
    ("job.demand_progress_gap", None): ("position_name", "gap"),
    ("job.demand_progress_gap", "project_group"): ("project_group", "gap"),
    ("match.threshold_reference", None): ("title", "ratio"),
    ("match.threshold_reference", "job"): ("title", "ratio"),
    ("jobs.active", "department"): ("department", "active_jobs"),
}

_MAX_SERIES = 20


def _chart_columns(metric_id: str, group_by: str | None) -> tuple[str, str] | None:
    return CHART_COLUMNS.get((metric_id, group_by))


def build_chart(
    metric_id: str,
    name: str,
    value: Any,
    table: dict | None,
    note: str | None,
    *,
    group_by: str | None = None,
) -> dict:
    """从查询结果构建图表规格。数据仅取自 table.rows（一致性的构造保证）。"""
    rows = (table or {}).get("rows") or []

    if note == "暂无数据" or (not rows and value is None):
        return {"chart_type": "none", "reason": "暂无数据"}

    if not rows:
        # 纯标量（如 jobs.active 无分组）：大数字卡
        return {"chart_type": "number", "label": name, "value": value}

    cols = _chart_columns(metric_id, group_by)
    if cols is None:
        return {"chart_type": "none", "reason": "该指标未配置绘图列"}
    label_col, value_col = cols
    if label_col not in (table or {}).get("columns", []):
        return {"chart_type": "none", "reason": "表格缺少可绘图列"}

    series = []
    for row in rows[:_MAX_SERIES]:
        v = row.get(value_col)
        # 非数值（含 None/bool/字符串）不绘图：宁可 none + 可读原因，也不渲染误导柱高
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return {"chart_type": "none", "reason": "数值列含非数值，无法绘图"}
        series.append({
            "name": str(row.get(label_col)),
            "value": float(v),
        })
    return {
        "chart_type": "bar",
        "x_field": "name",
        "y_field": "value",
        "data": series,
        "truncated_series": len(rows) > _MAX_SERIES,
    }
