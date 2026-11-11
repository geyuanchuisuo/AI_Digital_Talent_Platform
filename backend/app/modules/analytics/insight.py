"""决策建议解读（T-D10 ②）——事实段/推断段机器可区分。

结构：`{"segments": [...], "generator": "rule"}`，每段：
- `{"kind": "fact", "key": ..., "text": ..., "ref": {...}}`——事实段：只复述查询结果
  中的数字（ref 携带其引用的具体值，供机器核验与前端着色）；
- `{"kind": "inference", "key": ..., "text": ..., "basis": [fact_key...]}`——推断段：
  模板化建议，`basis` 显式引用其依据的事实段。

「不把相关说成因果」：推断模板全部使用建议/分布措辞（建议优先/建议关注/仅描述
分布），并用 `_CAUSAL_WORDS` 扫描守卫（测试断言 + 运行期 dev 断言）。规则式实现
不依赖 LLM——同一问题+同一数据 ⇒ 解读完全可复现（验收①）；LLM 增强留待 Key
就绪后按同一分段契约接入。

规则式生成器（generator="rule"）仅复述查询结果与模板化建议——不虚构任何数字。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: 因果措辞守卫词表（推断/事实段均不得出现——「不把相关说成因果」的运行期断言）
_CAUSAL_WORDS = ("导致", "因为", "由于", "造成", "使得", "归因于", "引发", "引起", "源于")

_SEGMENTS_MAX_FACTS = 5


def build_interpretation(
    metric_id: str,
    name: str,
    value: Any,
    table: dict | None,
    note: str | None,
    *,
    group_by: str | None = None,
    data_time: datetime,
) -> dict:
    """从查询结果构建分段解读。数据仅取自 value/table（可复现性）。"""
    rows = (table or {}).get("rows") or []
    segments: list[dict] = []
    gen = "rule"

    if note == "暂无数据" or (not rows and value is None):
        segments.append({
            "kind": "fact", "key": "no_data",
            "text": f"{name}：暂无数据。", "ref": {"metric_id": metric_id, "value": None},
        })
        return {"segments": segments, "generator": gen}

    if not rows:
        segments.append({
            "kind": "fact", "key": "headline",
            "text": f"{name}：{value}。",
            "ref": {"metric_id": metric_id, "value": value},
        })
        segments.append({
            "kind": "inference", "key": "suggest_dim",
            "text": "建议切换分组维度查看分布，或结合筛选条件缩小范围。",
            "basis": ["headline"],
        })
        return {"segments": segments, "generator": gen}

    if metric_id == "job.demand_progress_gap":
        cols = (table or {}).get("columns") or []
        label_col = "project_group" if "project_group" in cols else "position_name"
        return _gap_interpretation(value, rows, gen, label_col=label_col)
    if metric_id == "match.threshold_reference":
        return _threshold_interpretation(value, rows, gen)
    if metric_id == "jobs.active" and group_by == "department":
        return _active_jobs_by_department_interpretation(value, rows, gen)

    # 兜底：仅事实段
    segments.append({
        "kind": "fact", "key": "headline",
        "text": f"{name}：{value}。",
        "ref": {"metric_id": metric_id, "value": value},
    })
    return {"segments": segments, "generator": gen}


def _gap_interpretation(value: int, rows: list[dict], gen: str, *, label_col: str = "position_name") -> dict:
    segments: list[dict] = []
    total = int(sum(r["gap"] for r in rows))
    segments.append({
        "kind": "fact", "key": "headline",
        "text": f"在招需求 {len(rows)} 项，人才需求缺口合计 {total} 人（逐需求非负求和）。",
        "ref": {"metric_id": "job.demand_progress_gap", "value": total, "demand_count": len(rows)},
    })
    if rows and total > 0:
        top = max(rows, key=lambda r: r["gap"])
        top_label = str(top.get(label_col) or "未命名")
        if top["gap"] > 0:
            share = round(top["gap"] / total, 4)
            segments.append({
                "kind": "fact", "key": "top_gap",
                "text": f"缺口最大的一项为「{top_label}」，缺口 {top['gap']} 人。",
                "ref": {"demand_or_group": top_label, "gap": top["gap"], "share": share},
            })
            if len(rows) > 1 and share >= 0.5:
                segments.append({
                    "kind": "inference", "key": "focus_top",
                    "text": (f"缺口高度集中在「{top_label}」（占 {share:.0%}）——"
                             "建议优先补齐该岗位或评估需求拆分；此为分布特征描述，不构成因果判断。"),
                    "basis": ["top_gap", "headline"],
                })
        unfilled = [r for r in rows if r["headcount"] > r["gap"]]
        if unfilled:
            segments.append({
                "kind": "inference", "key": "suggest_fill",
                "text": "存在已录用未满编的需求，建议核对 HR 确认录用与在途到岗情况后再补员。",
                "basis": ["headline"],
            })
    if not any(s["kind"] == "inference" for s in segments):
        segments.append({
            "kind": "inference", "key": "suggest_monitor",
            "text": "当前各需求缺口均衡或为零，建议保持缺口预警监控。",
            "basis": ["headline"],
        })
    return {"segments": segments[:_SEGMENTS_MAX_FACTS * 2], "generator": gen}


def _threshold_interpretation(value: float, rows: list[dict], gen: str) -> dict:
    segments: list[dict] = []
    pct = f"{value:.0%}"
    total = int(sum(r["total"] for r in rows))
    reached = int(sum(r["reached"] for r in rows))
    segments.append({
        "kind": "fact", "key": "headline",
        "text": f"总体参考达线率 {pct}（{reached}/{total} 行，硬淘汰行已排除）。",
        "ref": {"metric_id": "match.threshold_reference", "value": value,
                "reached": reached, "total": total},
    })
    if rows:
        best = max(rows, key=lambda r: r["ratio"])
        worst = min(rows, key=lambda r: r["ratio"])
        segments.append({
            "kind": "fact", "key": "spread",
            "text": (f"按岗位分布：最高「{best['title']}」{best['ratio']:.0%}，"
                     f"最低「{worst['title']}」{worst['ratio']:.0%}。"),
            "ref": {"best": {"job_id": best["job_id"], "ratio": best["ratio"]},
                    "worst": {"job_id": worst["job_id"], "ratio": worst["ratio"]}},
        })
        if best["ratio"] != worst["ratio"]:
            segments.append({
                "kind": "inference", "key": "suggest_review",
                "text": ("岗位间达线率差异明显——建议先复核各岗位评分阈值设置与简历画像匹配度；"
                         "此为分布差异描述，达线率与录用结果的关系需另行验证，不构成因果结论。"),
                "basis": ["spread", "headline"],
            })
    return {"segments": segments, "generator": gen}


def _active_jobs_by_department_interpretation(value: int, rows: list[dict], gen: str) -> dict:
    segments: list[dict] = []
    segments.append({
        "kind": "fact", "key": "headline",
        "text": f"活跃岗位合计 {value} 个，分布于 {len(rows)} 个部门（含未指定）。",
        "ref": {"metric_id": "jobs.active", "value": value, "department_count": len(rows)},
    })
    if rows:
        top = max(rows, key=lambda r: r["active_jobs"])
        segments.append({
            "kind": "fact", "key": "top_department",
            "text": f"活跃岗位最多：「{top['department']}」（{top['active_jobs']} 个）。",
            "ref": {"department": top["department"], "active_jobs": top["active_jobs"]},
        })
        segments.append({
            "kind": "inference", "key": "suggest_balance",
            "text": "建议结合各部门招聘进度查看岗位分布——部门间数量差异提示关注招聘资源分配；"
                    "分布差异本身不构成部门表现的因果结论。",
            "basis": ["top_department", "headline"],
        })
    return {"segments": segments, "generator": gen}


def has_causal_language(segments: list[dict]) -> bool:
    """守卫扫描：任一段文本含因果措辞 → True（测试断言 + 运行期 dev 告警用）。"""
    for s in segments:
        text = str(s.get("text", ""))
        if any(w in text for w in _CAUSAL_WORDS):
            return True
    return False


# --------------------------------------------------------------------------- #
# LLM 增强（PRD D25）：规则解读打底 + LLM 增补推断段（因果守卫硬过滤）
# --------------------------------------------------------------------------- #

_LLM_ENHANCE_MAX_FACTS_IN_PROMPT = 5
_LLM_ENHANCE_MAX_INFERENCES = 2
_LLM_ENHANCE_TEXT_MAX = 200


def _llm_enhance_prompt(metric_id: str, name: str, rule_segments: list[dict]) -> str | None:
    """从规则分段提取事实部分拼 Prompt；无可用事实返回 None（不空转 LLM）。"""
    facts = [s for s in rule_segments if s.get("kind") == "fact"][:_LLM_ENHANCE_MAX_FACTS_IN_PROMPT]
    if not facts:
        return None
    lines = [f"指标：{name}（{metric_id}）"]
    for s in facts:
        lines.append(f"- 事实：{s['text']}")
    lines.append("")
    lines.append("请基于以上事实给出至多 2 条补充解读建议。要求：")
    lines.append("1. 只输出 JSON 对象 {\"items\": [{\"text\": \"...\", \"basis\": [依据的事实编号]}]}；")
    lines.append("2. 每条 ≤100 字，使用建议/描述措辞；")
    lines.append("3. 严禁出现以下因果措辞：导致、因为、由于、造成、使得、归因于、引发、引起、源于；")
    lines.append("4. 不得虚构任何数字，只能引用上述事实中出现的数值。")
    return "\n".join(lines)


class _EnhanceItem(BaseModel):
    text: str
    basis: list[str] = []


class _EnhanceList(BaseModel):
    items: list[_EnhanceItem]


def build_interpretation_with_llm(
    metric_id: str,
    name: str,
    value: Any,
    table: dict | None,
    note: str | None,
    *,
    group_by: str | None = None,
    data_time: datetime,
    enabled: bool | None = None,
) -> dict:
    """规则解读打底 + LLM 增补推断段（PRD D25）。

    - 规则段**永远在场**（可复现底线不依赖 LLM）；LLM 只增补 `inference` 段；
    - 增补段过同一因果守卫（`has_causal_language`）——违规整段丢弃，不留 Partial；
    - LLM 不可用/失败/超时 → 如实回退 `generator="rule"`（不假数据、不半残）；
    - `enabled=None` 读 `settings.analytics_llm_insight_enabled`（默认 False，部署方显式开启）。
    """
    from app.config import settings  # noqa: PLC0415 局部避免环

    result = build_interpretation(metric_id, name, value, table, note,
                                  group_by=group_by, data_time=data_time)
    if enabled is None:
        enabled = bool(getattr(settings, "analytics_llm_insight_enabled", False))
    if not enabled:
        return result

    from app.core import llm_client  # noqa: PLC0415

    prompt = _llm_enhance_prompt(metric_id, name, result["segments"])
    if prompt is None:
        return result
    try:
        raw = llm_client.chat_json(
            prompt, _EnhanceList, system_prompt="你是数据分析解读助手，只输出合法 JSON。",
            purpose="insight_llm_enhance", max_tokens=512, retry=0,
        )
    except Exception:  # noqa: BLE001 LLM 不可用/失败/超时如实回退（不假数据）
        logger.info("D25 LLM 增强解读不可用，回退规则式（metric=%s）", metric_id, exc_info=True)
        return result

    if not isinstance(raw, _EnhanceList):
        logger.warning("D25 LLM 返回结构非法，回退规则式（metric=%s）", metric_id)
        return result

    existing_keys = {s["key"] for s in result["segments"]}
    enhanced: list[dict] = []
    for item in raw.items[:_LLM_ENHANCE_MAX_INFERENCES]:
        text = (item.text or "").strip()[:_LLM_ENHANCE_TEXT_MAX]
        if not text:
            continue
        cand = {"kind": "inference", "key": f"llm_{len(enhanced)+1}",
                "text": text, "basis": list(item.basis or [])}
        # basis 只引用真实存在的事实段 key（LLM 给的编号不可信，过滤后无依据则丢弃）
        basis = [k for k in cand["basis"] if k in existing_keys]
        if not basis:
            logger.warning("D25 LLM 增补段无有效事实依据，整段丢弃：%s", text[:80])
            continue
        cand["basis"] = basis
        if has_causal_language([cand]):
            logger.warning("D25 LLM 增补段含因果措辞，整段丢弃：%s", text[:80])
            continue
        enhanced.append(cand)

    if not enhanced:
        return result
    return {"segments": result["segments"] + enhanced, "generator": "rule+llm"}
