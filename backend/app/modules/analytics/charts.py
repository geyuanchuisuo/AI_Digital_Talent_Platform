"""图表规格生成（T-D10 登记指标自动出图 + T-D-SELF-07 自由问数自动出图）。

图表数据**只从同一响应的 table 派生**（验收③：图表数据与表格一致——由构造保证，
另有测试逐点核对）。图表类型按数据形态确定性选择（不依赖 LLM）：

- 表无行且 value 非空 → `number`（大数字卡）
- 表有行且配置了 label/value 列 → `bar`（类目幅度对比；序列截断至 20 项防长尾）
- 暂无数据 / 无可绘图列 / 数值列含非数值 → `none`（前端不渲染图区，避免误导图形）

自由问数（nl2sql）无指标注册表，改用 `build_nl2sql_chart` 按**结果形态**推断绘图列，
产出与登记指标**同一规格契约**（`chart_render` 可直接消费）。
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


def _label(raw: Any) -> str:
    """类目标签统一口径：缺失→"-"（与 `chart_render._label_text` 一致，不显示 "None"）。"""
    return str(raw if raw is not None else "-")


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
            "name": _label(row.get(label_col)),
            "value": float(v),
        })
    return {
        "chart_type": "bar",
        "x_field": "name",
        "y_field": "value",
        "data": series,
        "truncated_series": len(rows) > _MAX_SERIES,
    }


def build_nl2sql_chart(columns: list[str], rows: list[dict]) -> dict:
    """自由问数结果 → 图表规格（T-D-SELF-07 / D23）。

    与登记指标的 `build_chart` 共用同一规格契约（`chart_render` 可直接消费），但列选择
    不依赖指标注册表，而按结果形态**确定性推断**：

    - 无行 → `none`（暂无数据）
    - 恰好 2 列 → `bar`（第 1 列类目、第 2 列数值；序列截断 20 项）
    - 恰好 1 列且仅 1 行 → `number`（单值卡）
    - 其余（列数不符、单列多行、数值列含非数值）→ `none` + 可读原因

    **不做「猜哪列是数值」的启发式**：形态不符即如实 `none`，宁可不出图也不渲染误导
    图形（与 `build_chart` 的数值守卫同口径：bool 视为非数值）。数值列在绘制窗口内出现
    非数值 → 整图 `none`（不静默丢点）。
    """
    rows = rows or []
    if not rows:
        return {"chart_type": "none", "reason": "暂无数据"}

    cols = list(columns or [])

    if len(cols) == 2:
        label_col, value_col = cols[0], cols[1]
        series = []
        for row in rows[:_MAX_SERIES]:
            v = row.get(value_col)
            if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                return {"chart_type": "none", "reason": "数值列含非数值，无法绘图"}
            series.append({"name": _label(row.get(label_col)), "value": float(v)})
        return {
            "chart_type": "bar",
            "x_field": "name",
            "y_field": "value",
            "data": series,
            "truncated_series": len(rows) > _MAX_SERIES,
        }

    if len(cols) == 1 and len(rows) == 1:
        v = rows[0].get(cols[0])
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return {"chart_type": "none", "reason": "数值列含非数值，无法绘图"}
        return {"chart_type": "number", "label": str(cols[0]), "value": v}

    return {"chart_type": "none", "reason": "结果列数/行数不支持自动绘图"}
