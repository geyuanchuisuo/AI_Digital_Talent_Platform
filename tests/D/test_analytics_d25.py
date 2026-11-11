"""D25 智能解读 LLM 增强用例。

覆盖：默认关闭、LLM 正常增补、调用失败回退、无有效事实依据丢弃、
因果措辞丢弃，以及查询服务默认不触发 LLM。

T-D-SELF-10 补强（隔离用例，不依赖真实网关）：
- 事实段可复算：ref 值与文本数字均能由输入 value/table 复算，不虚构；
- LLM 返回非 _EnhanceList / 空 items → 如实回退 generator="rule"；
- 超长文本截断、空白文本跳过、增补段上限 2 条、basis 部分有效则过滤保留；
- 无事实段时 Prompt 为 None（不空转 LLM）。
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from app.config import settings
from app.core import llm_client as llm_client_module
from app.modules.analytics import insight
from app.modules.analytics.insight import _EnhanceItem, _EnhanceList


def _rule_result() -> dict:
    return insight.build_interpretation(
        "job.demand_progress_gap",
        "人才需求缺口",
        5,
        {
            "columns": ["position_name", "headcount", "gap"],
            "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}],
        },
        None,
        data_time=datetime(2026, 11, 11, 12, 0, 0),
    )


def test_disabled_returns_rule_result(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - 若被调用测试即失败
        raise AssertionError("关闭开关时不得调用 LLM")

    monkeypatch.setattr(llm_client_module, "chat_json", _boom)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=False,
    )
    assert result["generator"] == "rule"


def test_default_setting_is_disabled(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - 若被调用测试即失败
        raise AssertionError("默认配置不得调用 LLM")

    monkeypatch.setattr(llm_client_module, "chat_json", _boom)
    monkeypatch.setattr(settings, "analytics_llm_insight_enabled", False)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11),
    )
    assert result["generator"] == "rule"


def test_llm_enhancement_appends_inference(monkeypatch):
    raw = _EnhanceList(items=[_EnhanceItem(text="建议关注缺口集中岗位的配置合理性。", basis=["headline"])])

    def _chat(*args, **kwargs):
        return raw

    monkeypatch.setattr(llm_client_module, "chat_json", _chat)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    assert result["generator"] == "rule+llm"
    assert result["segments"][0]["kind"] == "fact"
    assert result["segments"][-1]["kind"] == "inference"
    assert result["segments"][-1]["basis"] == ["headline"]
    assert not insight.has_causal_language(result["segments"])


def test_llm_failure_falls_back_to_rule(monkeypatch):
    def _chat(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(llm_client_module, "chat_json", _chat)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    assert result["generator"] == "rule"


def test_invalid_basis_discards_item(monkeypatch):
    raw = _EnhanceList(items=[_EnhanceItem(text="建议关注缺口集中岗位。", basis=["not_a_fact"])])

    def _chat(*args, **kwargs):
        return raw

    monkeypatch.setattr(llm_client_module, "chat_json", _chat)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    assert result["generator"] == "rule"
    assert all(s["key"] != "llm_1" for s in result["segments"])


def test_causal_language_discards_item(monkeypatch):
    raw = _EnhanceList(items=[_EnhanceItem(text="缺口集中导致招聘风险上升。", basis=["headline"])])

    def _chat(*args, **kwargs):
        return raw

    monkeypatch.setattr(llm_client_module, "chat_json", _chat)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    assert result["generator"] == "rule"
    assert all("导致" not in s["text"] for s in result["segments"])


# ---- T-D-SELF-10：事实段可复算（不虚构数字）----

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _norm_num(x) -> str:
    f = float(x)
    return str(int(f)) if f == int(f) else str(f)


def _numbers_in(text) -> set[str]:
    return set(_NUMBER_RE.findall(str(text)))


def _gap_allowed_numbers(value, rows) -> set[str]:
    """输入数字 + 该场景的合法派生量（行数、缺口合计、占比及其百分数）。"""
    allowed = {_norm_num(value)}
    for r in rows:
        for v in r.values():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                allowed.add(_norm_num(v))
            elif isinstance(v, str):
                allowed |= _numbers_in(v)
    allowed.add(_norm_num(len(rows)))
    total = sum(r["gap"] for r in rows)
    allowed.add(_norm_num(total))
    if total:
        top = max(rows, key=lambda r: r["gap"])
        share = round(top["gap"] / total, 4)
        allowed.add(_norm_num(share))
        allowed.add(str(int(round(share * 100))))
    return allowed


def test_gap_fact_refs_reproducible_from_input():
    """事实段 ref 必须能由输入 value/table 复算（机器可核验，不虚构）。"""
    rows = [
        {"position_name": "后端工程师", "headcount": 2, "gap": 5},
        {"position_name": "前端工程师", "headcount": 1, "gap": 3},
    ]
    table = {"columns": ["position_name", "headcount", "gap"], "rows": rows}
    result = insight.build_interpretation(
        "job.demand_progress_gap", "人才需求缺口", 8, table, None,
        data_time=datetime(2026, 1, 1),
    )
    by_key = {s["key"]: s for s in result["segments"]}
    total = sum(r["gap"] for r in rows)
    top = max(rows, key=lambda r: r["gap"])

    assert by_key["headline"]["ref"]["value"] == total
    assert by_key["headline"]["ref"]["demand_count"] == len(rows)
    assert by_key["top_gap"]["ref"]["gap"] == top["gap"]
    assert by_key["top_gap"]["ref"]["demand_or_group"] == top["position_name"]
    assert by_key["top_gap"]["ref"]["share"] == round(top["gap"] / total, 4)


def test_gap_fact_text_numbers_all_traceable():
    """事实段文本中出现的每个数字，都必须来自输入或合法派生量。"""
    rows = [
        {"position_name": "后端工程师", "headcount": 2, "gap": 5},
        {"position_name": "前端工程师", "headcount": 1, "gap": 3},
    ]
    table = {"columns": ["position_name", "headcount", "gap"], "rows": rows}
    result = insight.build_interpretation(
        "job.demand_progress_gap", "人才需求缺口", 8, table, None,
        data_time=datetime(2026, 1, 1),
    )
    allowed = _gap_allowed_numbers(8, rows)
    for seg in result["segments"]:
        if seg["kind"] != "fact":
            continue
        extra = _numbers_in(seg["text"]) - allowed
        assert not extra, f"事实段出现不可溯源数字 {extra}：{seg['text']}"


def test_threshold_and_active_fact_refs_reproducible():
    """达线率与活跃岗位场景：ref 同样可复算。"""
    t_rows = [{"job_id": 1, "title": "后端", "ratio": 0.8, "reached": 8, "total": 10},
              {"job_id": 2, "title": "前端", "ratio": 0.5, "reached": 5, "total": 10}]
    t_table = {"columns": ["job_id", "title", "ratio", "reached", "total"], "rows": t_rows}
    t_result = insight.build_interpretation(
        "match.threshold_reference", "达线率参考", 0.65, t_table, None,
        data_time=datetime(2026, 1, 1),
    )
    t_by = {s["key"]: s for s in t_result["segments"]}
    assert t_by["headline"]["ref"]["reached"] == sum(r["reached"] for r in t_rows)
    assert t_by["headline"]["ref"]["total"] == sum(r["total"] for r in t_rows)

    a_rows = [{"department": "技术部", "active_jobs": 4},
              {"department": "产品部", "active_jobs": 2}]
    a_table = {"columns": ["department", "active_jobs"], "rows": a_rows}
    a_result = insight.build_interpretation(
        "jobs.active", "活跃岗位", 6, a_table, None, group_by="department",
        data_time=datetime(2026, 1, 1),
    )
    a_by = {s["key"]: s for s in a_result["segments"]}
    assert a_by["headline"]["ref"]["value"] == sum(r["active_jobs"] for r in a_rows)
    assert a_by["headline"]["ref"]["department_count"] == len(a_rows)


# ---- T-D-SELF-10：LLM 返回形状与边界 ----

def test_llm_non_enhance_list_returns_fallback(monkeypatch):
    """LLM 返回非 _EnhanceList（dict/字符串/None）→ 如实回退，不报错不半残。"""
    for bad in ({"items": []}, "建议关注", None, ["建议"]):
        monkeypatch.setattr(llm_client_module, "chat_json", lambda *a, **k: bad)
        result = insight.build_interpretation_with_llm(
            "job.demand_progress_gap", "人才需求缺口", 5,
            {"columns": ["position_name", "headcount", "gap"],
             "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
            None, data_time=datetime(2026, 11, 11), enabled=True,
        )
        assert result["generator"] == "rule", f"非法返回 {bad!r} 未回退"
        assert all(s["key"] != "llm_1" for s in result["segments"])


def test_llm_empty_items_returns_fallback(monkeypatch):
    monkeypatch.setattr(llm_client_module, "chat_json",
                        lambda *a, **k: _EnhanceList(items=[]))
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    assert result["generator"] == "rule"


def test_llm_long_text_truncated_and_blank_skipped(monkeypatch):
    raw = _EnhanceList(items=[
        _EnhanceItem(text="建" * 300, basis=["headline"]),
        _EnhanceItem(text="   ", basis=["headline"]),
    ])
    monkeypatch.setattr(llm_client_module, "chat_json", lambda *a, **k: raw)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    assert result["generator"] == "rule+llm"
    llm_segs = [s for s in result["segments"] if s["key"].startswith("llm_")]
    assert len(llm_segs) == 1, "空白文本段应被跳过"
    assert len(llm_segs[0]["text"]) == insight._LLM_ENHANCE_TEXT_MAX


def test_llm_more_than_two_items_truncated_to_two(monkeypatch):
    raw = _EnhanceList(items=[
        _EnhanceItem(text=f"建议 {i}。", basis=["headline"]) for i in range(5)
    ])
    monkeypatch.setattr(llm_client_module, "chat_json", lambda *a, **k: raw)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    llm_segs = [s for s in result["segments"] if s["key"].startswith("llm_")]
    assert len(llm_segs) == insight._LLM_ENHANCE_MAX_INFERENCES
    assert [s["key"] for s in llm_segs] == ["llm_1", "llm_2"]


def test_llm_partial_valid_basis_filtered_not_dropped(monkeypatch):
    """basis 部分有效：过滤掉不存在的 key，保留有效依据（不整段丢弃）。"""
    raw = _EnhanceList(items=[_EnhanceItem(
        text="建议关注缺口集中岗位。", basis=["bogus", "headline", "also_bogus"])])
    monkeypatch.setattr(llm_client_module, "chat_json", lambda *a, **k: raw)
    result = insight.build_interpretation_with_llm(
        "job.demand_progress_gap", "人才需求缺口", 5,
        {"columns": ["position_name", "headcount", "gap"],
         "rows": [{"position_name": "后端工程师", "headcount": 5, "gap": 5}]},
        None, data_time=datetime(2026, 11, 11), enabled=True,
    )
    llm_segs = [s for s in result["segments"] if s["key"].startswith("llm_")]
    assert len(llm_segs) == 1
    assert llm_segs[0]["basis"] == ["headline"]


def test_prompt_none_without_fact_segments():
    """无事实段时 Prompt 为 None（不空转 LLM）。"""
    assert insight._llm_enhance_prompt("m", "n", []) is None
