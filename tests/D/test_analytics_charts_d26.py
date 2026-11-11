"""T-D-SELF-12 / D26 网页图表一致性补强。

在 `test_analytics_td10.py`（服务层一致性）之上补「图表规格」专项：
①逐点一致性：每个已登记绘图列组合，chart.data 与 table.rows 逐点相等；
②截断：>20 项截断并置 truncated_series；边界 20/21；
③空结果：暂无数据 / 无行且 value 空 → none + 可读原因；
④单序列：1 行也出 bar（1 点）；
⑤非登记维度/缺列：未配置绘图列 → none + 原因（服务层另有 422 白名单）；
⑥非数值守卫：数值列含 None/bool/字符串 → none（不渲染误导柱高）。

运行：pytest tests/D/test_analytics_charts_d26.py -q（纯函数，无外部依赖）。
"""

from __future__ import annotations

import pytest

from app.modules.analytics.charts import CHART_COLUMNS, _MAX_SERIES, build_chart


def _table(columns: list[str], rows: list[dict]) -> dict:
    return {"columns": columns, "rows": rows}


# ---------------------------------------------------------------- ①逐点一致性

@pytest.mark.parametrize(
    "metric_id,group_by,label_col,value_col",
    [
        ("job.demand_progress_gap", None, "position_name", "gap"),
        ("job.demand_progress_gap", "project_group", "project_group", "gap"),
        ("match.threshold_reference", None, "title", "ratio"),
        ("match.threshold_reference", "job", "title", "ratio"),
        ("jobs.active", "department", "department", "active_jobs"),
    ],
)
def test_chart_data_matches_table_point_by_point(metric_id, group_by, label_col, value_col):
    rows = [{label_col: f"项{i}", value_col: i + 0.5} for i in range(5)]
    table = _table([label_col, value_col], rows)
    chart = build_chart(metric_id, "指标", 12.5, table, None, group_by=group_by)
    assert chart["chart_type"] == "bar"
    assert chart["data"] == [{"name": f"项{i}", "value": i + 0.5} for i in range(5)]
    assert chart["truncated_series"] is False
    assert chart["x_field"] == "name" and chart["y_field"] == "value"


def test_all_registered_chart_columns_are_covered():
    """登记绘图列组合与测试矩阵一致（防漏项/防登记了不可绘图的列）。"""
    assert set(CHART_COLUMNS) == {
        ("job.demand_progress_gap", None),
        ("job.demand_progress_gap", "project_group"),
        ("match.threshold_reference", None),
        ("match.threshold_reference", "job"),
        ("jobs.active", "department"),
    }


# ---------------------------------------------------------------- ②截断

def test_series_truncated_at_20():
    rows = [{"department": f"部门{i}", "active_jobs": i} for i in range(25)]
    chart = build_chart("jobs.active", "活跃岗位数", 300,
                        _table(["department", "active_jobs"], rows), None,
                        group_by="department")
    assert chart["chart_type"] == "bar"
    assert len(chart["data"]) == _MAX_SERIES == 20
    assert chart["truncated_series"] is True


@pytest.mark.parametrize("n,truncated", [(19, False), (20, False), (21, True)])
def test_series_truncation_boundary(n, truncated):
    rows = [{"department": f"部门{i}", "active_jobs": i} for i in range(n)]
    chart = build_chart("jobs.active", "活跃岗位数", n,
                        _table(["department", "active_jobs"], rows), None,
                        group_by="department")
    assert chart["truncated_series"] is truncated
    assert len(chart["data"]) == min(n, _MAX_SERIES)


# ---------------------------------------------------------------- ③空结果

def test_none_when_note_says_no_data():
    chart = build_chart("jobs.active", "活跃岗位数", None, _table([], []), "暂无数据")
    assert chart == {"chart_type": "none", "reason": "暂无数据"}


def test_none_when_no_rows_and_no_value():
    chart = build_chart("jobs.active", "活跃岗位数", None, _table(["department"], []), None)
    assert chart["chart_type"] == "none"


def test_number_card_when_scalar_without_rows():
    chart = build_chart("jobs.active", "活跃岗位数", 7, _table(["department"], []), None)
    assert chart == {"chart_type": "number", "label": "活跃岗位数", "value": 7}


# ---------------------------------------------------------------- ④单序列

def test_single_series_still_bar():
    chart = build_chart("jobs.active", "活跃岗位数", 1,
                        _table(["department", "active_jobs"],
                               [{"department": "技术部", "active_jobs": 1}]),
                        None, group_by="department")
    assert chart["chart_type"] == "bar"
    assert chart["data"] == [{"name": "技术部", "value": 1.0}]
    assert chart["truncated_series"] is False


# ---------------------------------------------------------------- ⑤非登记维度 / 缺列

def test_none_when_dimension_not_registered():
    """未登记绘图列的组合（如给无分组指标硬传分组）→ none + 原因，不出图。"""
    rows = [{"department": "技术部", "active_jobs": 2}]
    chart = build_chart("job.demand_progress_gap", "缺口", 2,
                        _table(["department", "active_jobs"], rows), None,
                        group_by="department")
    assert chart == {"chart_type": "none", "reason": "该指标未配置绘图列"}


def test_none_when_label_column_missing_from_table():
    rows = [{"active_jobs": 2}]
    chart = build_chart("jobs.active", "活跃岗位数", 2,
                        _table(["active_jobs"], rows), None, group_by="department")
    assert chart == {"chart_type": "none", "reason": "表格缺少可绘图列"}


def test_none_when_metric_has_no_chart_columns_at_all():
    rows = [{"department": "技术部", "active_jobs": 2}]
    chart = build_chart("some.unregistered.metric", "未知", 2,
                        _table(["department", "active_jobs"], rows), None)
    assert chart["chart_type"] == "none"


# ---------------------------------------------------------------- ⑥非数值守卫

@pytest.mark.parametrize("bad", [None, True, False, "N/A", "", [1], {"v": 1}])
def test_none_when_value_column_not_numeric(bad):
    rows = [{"department": "技术部", "active_jobs": 2},
            {"department": "销售部", "active_jobs": bad}]
    chart = build_chart("jobs.active", "活跃岗位数", 3,
                        _table(["department", "active_jobs"], rows), None,
                        group_by="department")
    assert chart == {"chart_type": "none", "reason": "数值列含非数值，无法绘图"}


def test_none_when_non_numeric_beyond_truncation_window_is_ignored():
    """截断窗口之外的坏值不影响已绘制的前 20 点（一致性口径：只核对渲染出的点）。"""
    rows = [{"department": f"部门{i}", "active_jobs": i} for i in range(20)]
    rows.append({"department": "坏行", "active_jobs": "N/A"})
    chart = build_chart("jobs.active", "活跃岗位数", 190,
                        _table(["department", "active_jobs"], rows), None,
                        group_by="department")
    assert chart["chart_type"] == "bar"
    assert len(chart["data"]) == 20 and chart["truncated_series"] is True
