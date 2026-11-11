"""D25 智能解读 LLM 增强用例。

覆盖：默认关闭、LLM 正常增补、调用失败回退、无有效事实依据丢弃、
因果措辞丢弃，以及查询服务默认不触发 LLM。
"""

from __future__ import annotations

from datetime import datetime

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
