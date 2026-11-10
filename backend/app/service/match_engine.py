"""匹配/筛选共用评分引擎（T-23）· 需求口径以 `docs/需求文档.md` V2.6 §5（F03）为准。

**输入文档**：`docs/需求文档.md` V2.6 §5 / §12.2（冻结基线，优先级最高）、
`docs/modules/A/设计方案.md` M4、Prompt C（`core/prompts/match.txt`）。
旧 `docs/算法与判定规则.md` v1.2 仅存于 `docs/imports/` 历史副本，其硬淘汰与
固定公式口径**不得**覆盖 V2.6；本轮 A02-1 已纠正硬淘汰，公式部分见下方待办。

三条硬口径（改动前先读）：
1. **D-A：筛选（F1）与匹配（F3）共用本引擎，`use_vector` 区分**——
   筛选传 `False`：`experience = llm_experience`，**全程不触碰 Embedding/Milvus**
   （验收点要求断言零调用）；匹配传 `True`：`experience = 0.7×llm + 0.3×similarity×100`。
2. **规则层核对不再淘汰（A02-1，V2.6 §5）**：学历排序值 / 年限 / 必备技能逐元素
   命中仍由 `hard_gate()` 计算，但结果只作「不符项」与「待核实」提示，随 Prompt C
   输入一并交给模型；**所有候选人一律进入评分**，不再有筛选 `score=NULL` /
   匹配 `score=0` 的硬淘汰分支，`hard_fail` 恒 False。依据：V2.6 §5「硬条件或
   分数线不符合者仍显示不符项与资料缺口，不自动关闭申请」「缺失证据不直接判为
   不满足」「低分不自动淘汰」。
3. **降级不失败**（D-B/§4.2）：匹配路径 Milvus **或** Embedding 不可用（含岗位向量
   缺失、画像 `vector_status != synced`）→ `experience = llm_experience`、
   `vector_similarity=None`、`vector_degraded=True`，批次照常执行。

**A02-2 待办（本轮未做，跨模块阻塞）**：V2.6 §5 明确「语义相似度不等于业务匹配分，
不强制加入 0.7/0.3 或 40/30/15/15 的公式」，设计方案 M4 要求改为模型直接给
0–100 综合分。该改动需 `MatchScoreOutput` 增 `overall_score`，而 Prompt C 的
`PROMPT_C_SCHEMA` 与 `test_prompts.py` 归 D 维护，A 不越界修改；已发
`docs/requests/A/20261110-D-A02-2-PromptC综合分契约.md`，待 D 响应后实施。

输入约定（worker 负责从 ORM 行转 dict）：
- `job`：批次 `config_snapshot_json`，键含 `title/jd_text/requirements_text/min_degree/
  min_work_years/must_skills/bonus_skills/hard_rule_switches/weights/pass_threshold/pending_threshold`
  （T-24/T-25 建快照时与 `job_profile_service` 输出同源；技能已是归一化规范名，
  引擎内仍会再过一次 `normalize_skill`，幂等）。
- `profile`：键含 `talent_profile_id/degree/work_years/skills_json/vector_status`，
  可选 `name/current_title/tags_json/ability_scores_json/experience_summary`（拼 Prompt C 输入）。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.core.llm_client import chat_json
from app.core.normalize import (
    DEGREE_VALUES,
    any_skill_hit,
    degree_rank,
    normalize_skill,
    skill_variant_names,
)
from app.schemas.match import DIM_KEYS, MatchScoreOutput

logger = logging.getLogger(__name__)

#: F3 经历维度合成系数（算法 §4.2）
EXPERIENCE_LLM_WEIGHT = 0.7
EXPERIENCE_VECTOR_WEIGHT = 0.3

VECTOR_DEGRADED_NOTE = "向量依赖不可用，经历维度仅按 LLM 评分"

#: A02-1：规则层核对结果在 `misses` 中的前缀（供页面展示「不符项 / 资料缺口」）。
RULE_GAP_PREFIX = "不符项："
PENDING_VERIFICATION_PREFIX = "待核实："
#: 这些信息只是提示，不是淘汰结论；拼进 Prompt C 输入时明确告知模型不得据此扣分。
RULE_REVIEW_NOTICE = (
    "以下规则层核对结果仅作参考，不是淘汰结论：不符项与证据缺失仍须给出评分与依据，"
    "缺失证据须标注待核实，不得推断为「没有」或据此判为显著不匹配。"
)


@dataclass
class EngineResult:
    """单候选人评分结果；worker 据此落 `screen_results` / `match_results`。

    A02-1 起 `hard_fail` 恒 False、`hard_fail_reason` 恒 None：DB/接口字段保留
    兼容历史数据，但引擎不再产生自动淘汰。不符项与待核实项统一进 `misses`。
    """

    hard_fail: bool
    hard_fail_reason: str | None
    #: 综合分（0–100，保留 2 位）；所有候选人均有分，不再有 NULL/0 的硬淘汰值
    score: float | None
    dimension_scores: dict[str, float]
    vector_similarity: float | None
    vector_degraded: bool
    hits: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def llm_called(self) -> bool:
        return bool(self.dimension_scores)


# --------------------------------------------------------------------------- #
# 硬门槛（算法 §1/§2/§3；规则化零 LLM）
# --------------------------------------------------------------------------- #
def hard_gate(job: Mapping[str, Any], profile: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """硬门槛判定。返回 `(reasons, hits, misses)`——reasons 非空即硬淘汰。

    - 学历（开关 degree）：缺失/无法识别（排序值 0）或 `candidate_rank < required_rank`
      → 不达标（算法 §1：比较用排序值，严禁字符串比较）；
    - 年限（开关 work_years）：完全缺失 → "工作年限缺失"；低于要求 → 不达标
      （候选人年限取画像已归一存储的 `work_years`，T-16 已做区间合并 + 实习过滤）；
    - 必备技能（开关 must_skills）：逐元素独立判定（D-G），**缺失项全部列出**；
      候选人技能列表为空 → "技能列表为空"；
    - bonus_skills 只进 hits，不参与淘汰（算法 §3.7）。
    """
    reasons: list[str] = []
    hits: list[str] = []
    misses: list[str] = []
    switches = job.get("hard_rule_switches") or {}

    # —— 学历 ——
    required_degree = job.get("min_degree")
    if switches.get("degree", True) and required_degree in DEGREE_VALUES:
        required_rank = degree_rank(required_degree)
        candidate_degree = profile.get("degree")
        candidate_rank = degree_rank(candidate_degree)
        if candidate_rank == 0:
            reasons.append("学历缺失或无法识别")
            misses.append("学历缺失或无法识别")
        elif candidate_rank < required_rank:
            reasons.append(f"学历不满足：要求 {required_degree}，候选人 {candidate_degree}")
            misses.append(f"学历不满足：要求 {required_degree}，候选人 {candidate_degree}")
        else:
            hits.append(f"学历达标（{candidate_degree}）")

    # —— 年限 ——
    min_years = job.get("min_work_years")
    if switches.get("work_years", True) and min_years is not None:
        work_years = profile.get("work_years")
        if work_years is None:
            reasons.append("工作年限缺失")
            misses.append("工作年限缺失")
        else:
            years = float(work_years)
            if years + 1e-9 < float(min_years):
                reasons.append(f"工作年限不足：要求 {float(min_years):g} 年，候选人 {years:g} 年")
                misses.append(f"工作年限不足：要求 {float(min_years):g} 年，候选人 {years:g} 年")
            else:
                hits.append(f"工作年限达标（{years:g} 年）")

    # —— 必备技能 ——
    must_skills = job.get("must_skills") or []
    if switches.get("must_skills", True) and must_skills:
        candidate_skills = profile.get("skills_json") or []
        if not candidate_skills:
            reasons.append("技能列表为空")
            misses.append("技能列表为空")
        else:
            for skill in must_skills:
                hit_element = any_skill_hit(skill, candidate_skills)
                if hit_element is None:
                    misses.append(f"缺失必备技能：{normalize_skill(skill)}")
                else:
                    hits.append(f"命中必备技能 {normalize_skill(skill)}（候选人：{hit_element}）")
            missing_count = sum(1 for m in misses if m.startswith("缺失必备技能"))
            if missing_count:
                reasons.append(f"必备技能未全部命中（缺 {missing_count} 项）")

    # —— 加分技能（只进 hits，不淘汰）——
    for skill in job.get("bonus_skills") or []:
        hit_element = any_skill_hit(skill, profile.get("skills_json") or [])
        if hit_element is not None:
            hits.append(f"命中加分技能 {normalize_skill(skill)}（候选人：{hit_element}）")

    return reasons, hits, misses


def classify_rule_gaps(reasons: Sequence[str]) -> tuple[list[str], list[str]]:
    """A02-1：把规则层核对结果分成「不符项」与「待核实」，均**不是**淘汰结论。

    V2.6 §5：「缺失证据不直接判为不满足」。故 `学历缺失/年限缺失/技能列表为空`
    这类「资料没有」的情形标 `待核实：`；`学历不满足/年限不足/必备技能未命中`
    这类明确比对不符的情形标 `不符项：`。两类都会进 `misses` 供页面展示，
    并随 Prompt C 输入交给模型参考，但不再阻止评分、不再把分数置 NULL/0。
    """
    gaps: list[str] = []
    pending: list[str] = []
    for reason in reasons:
        if "缺失" in reason or "为空" in reason:
            pending.append(PENDING_VERIFICATION_PREFIX + reason)
        else:
            gaps.append(RULE_GAP_PREFIX + reason)
    return gaps, pending


def build_rule_review_text(reasons: Sequence[str]) -> str:
    """拼进 Prompt C 输入的规则层核对段（所有候选人都带，含无不符项时）。"""
    gaps, pending = classify_rule_gaps(reasons)
    strip_gap = len(RULE_GAP_PREFIX)
    strip_pending = len(PENDING_VERIFICATION_PREFIX)
    lines = [
        "【规则层核对（仅作参考，不是淘汰结论）】",
        "不符项：" + ("；".join(item[strip_gap:] for item in gaps) or "无"),
        "待核实：" + ("；".join(item[strip_pending:] for item in pending) or "无"),
        RULE_REVIEW_NOTICE,
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Prompt C 输入拼装
# --------------------------------------------------------------------------- #
def _evidence_summary(profile: Mapping[str, Any]) -> str | None:
    """从结构化解析数据生成经历证据摘要（H-A07 修复，2026-11-07）。

    输入结构对齐 B 契约（REQ-B 档案契约 §5）：`educations` / `employments` /
    `projects` 数组来自 `parsed_resumes.structured_json`。任何一段都截断防
    prompt 超长；三类全缺返回 None（由调用方标注证据缺失）。
    """
    blocks: list[str] = []
    educations = profile.get("educations") or []
    edu_lines = [
        f"- {e.get('school', '')} {e.get('major', '')} {e.get('degree', '')}（{e.get('start', '')}-{e.get('end', '')}）".strip()
        for e in educations
        if isinstance(e, Mapping)
    ]
    if edu_lines:
        blocks.append("教育经历：\n" + "\n".join(edu_lines[:5]))

    employments = profile.get("employments") or []
    emp_lines = []
    for e in employments[:5]:
        if not isinstance(e, Mapping):
            continue
        desc = str(e.get("description") or "").strip()
        line = f"- {e.get('company', '')} {e.get('title', '')}（{e.get('start', '')}-{e.get('end', '')}）"
        if desc:
            line += f"：{desc[:120]}"
        emp_lines.append(line)
    if emp_lines:
        blocks.append("工作经历：\n" + "\n".join(emp_lines))

    projects = profile.get("projects") or []
    proj_lines = []
    for p in projects[:5]:
        if not isinstance(p, Mapping):
            continue
        desc = str(p.get("description") or "").strip()
        line = f"- {p.get('name', '')}（{p.get('role', '')}，{p.get('start', '')}-{p.get('end', '')}）"
        if desc:
            line += f"：{desc[:120]}"
        proj_lines.append(line)
    if proj_lines:
        blocks.append("项目经历：\n" + "\n".join(proj_lines))

    return "\n\n".join(blocks) if blocks else None


def build_candidate_summary(profile: Mapping[str, Any]) -> str:
    """候选人画像摘要（Prompt C 输入）。

    经历证据优先级：显式 `experience_summary` > `educations/employments/projects`
    结构化生成（H-A07 修复：此前 worker 只传 5 字段，经历证据永远缺失）。
    `missing_fields` 非空时标注证据缺失，提示模型按缺失处理、不得推断「没有」
    （T-A34 边界：缺失证据标待核实）。
    """
    parts = [
        str(profile.get("name") or "未知"),
        str(profile.get("degree") or "学历未知"),
        f"工作年限 {float(profile['work_years']):g} 年" if profile.get("work_years") is not None else "工作年限未知",
        "技能：" + ("、".join(profile.get("skills_json") or []) or "无"),
    ]
    tags = profile.get("tags_json") or []
    if tags:
        parts.append("标签：" + "、".join(str(tag) for tag in tags[:15]))
    ability = profile.get("ability_scores_json") or {}
    if ability:
        ability_text = "；".join(f"{key} {dim.get('score')}" for key, dim in ability.items() if isinstance(dim, Mapping))
        if ability_text:
            parts.append("五维能力：" + ability_text)
    summary = profile.get("experience_summary")
    if not summary:
        summary = _evidence_summary(profile)
    if summary:
        parts.append("经历证据：\n" + str(summary)[:2000])
    missing = profile.get("missing_fields") or []
    if missing:
        parts.append("证据缺失（按缺失处理，不得推断为「没有」）：" + "、".join(str(m) for m in missing))
    if profile.get("current_title"):
        parts.append(f"当前职位：{profile['current_title']}")
    return "\n".join(parts)


def build_job_summary(job: Mapping[str, Any]) -> str:
    """岗位条件摘要（Prompt C 输入；与 config_snapshot 同源字段）。"""
    lines = [
        f"岗位：{job.get('title') or '未知'}",
        f"职责描述：{job.get('jd_text') or '无'}",
        f"任职要求：{job.get('requirements_text') or '无'}",
    ]
    if job.get("min_degree"):
        lines.append(f"最低学历：{job['min_degree']}")
    if job.get("min_work_years") is not None:
        lines.append(f"最低年限：{float(job['min_work_years']):g} 年")
    lines.append("必备技能：" + ("、".join(job.get("must_skills") or []) or "无"))
    lines.append("加分技能：" + ("、".join(job.get("bonus_skills") or []) or "无"))
    weights = job.get("weights") or {}
    if weights:
        lines.append(
            "评分权重：" + "、".join(f"{key} {value}" for key, value in weights.items())
        )
    return "\n".join(lines)


def build_job_vector_text(job: Mapping[str, Any]) -> str:
    """岗位侧向量文本（算法 §6.3：`title + jd_text + requirements_text + must_skills + bonus_skills`）。

    批次内只调用一次 embedding 并复用（T-25 worker 负责）。
    """
    return "\n".join(
        [
            str(job.get("title") or ""),
            str(job.get("jd_text") or ""),
            str(job.get("requirements_text") or ""),
            "必备技能:" + ",".join(job.get("must_skills") or []),
            "加分技能:" + ",".join(job.get("bonus_skills") or []),
        ]
    )


# --------------------------------------------------------------------------- #
# 向量相似度（仅 use_vector=True）
# --------------------------------------------------------------------------- #
def cosine_similarity(vector_a: Sequence[float], vector_b: Sequence[float]) -> float | None:
    """COSINE 相似度，clamp 到 [0,1]；维度不符或零向量返回 None（按降级处理）。"""
    if len(vector_a) != len(vector_b) or not vector_a:
        return None
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a == 0 or norm_b == 0:
        return None
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def evaluate(
    job: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    use_vector: bool,
    job_vector: Sequence[float] | None = None,
) -> EngineResult:
    """评分单候选人。异常语义：LLM 调用失败**原样上抛**（worker 决定批次失败与重试）。

    - `use_vector=False`（筛选）：不触碰 Embedding/Milvus（含降级分支），
      `vector_similarity` 恒 None、`vector_degraded` 恒 False；
    - `use_vector=True`（匹配）：岗位向量由 worker 批次内算一次传入 `job_vector`；
      画像向量经 `milvus_store.get_profile_vector` 取（不可用/缺失/非 synced → 降级）。
    """
    reasons, hits, misses = hard_gate(job, profile)
    gaps, pending = classify_rule_gaps(reasons)

    # A02-1（V2.6 §5）：规则层核对结果只作「不符项 / 待核实」提示，**不再提前淘汰**。
    # 所有候选人都进 Prompt C，包括硬条件不符与资料缺失者；分数不再置 NULL/0。
    # —— Prompt C 四维评分（temperature=0，chat_json 内置结构化重试）——
    user_text = (
        f"【候选人画像摘要】\n{build_candidate_summary(profile)}\n\n"
        f"【岗位条件】\n{build_job_summary(job)}\n\n"
        f"{build_rule_review_text(reasons)}"
    )
    output = chat_json(user_text, MatchScoreOutput, prompt_name="match")

    dimensions: dict[str, float] = {}
    llm_hits: list[str] = []
    llm_misses: list[str] = []
    for key in DIM_KEYS:
        dim = getattr(output, key)
        dimensions[key] = float(max(0, min(100, int(dim.score))))
        llm_hits.extend(str(item) for item in dim.hits)
        llm_misses.extend(str(item) for item in dim.misses)

    # —— 向量相似度（仅匹配）——
    vector_similarity: float | None = None
    vector_degraded = False
    if use_vector:
        vector_similarity, vector_degraded = _profile_similarity(profile, job_vector)
    experience_llm = dimensions["experience"]
    if use_vector and vector_similarity is not None:
        experience = EXPERIENCE_LLM_WEIGHT * experience_llm + EXPERIENCE_VECTOR_WEIGHT * (vector_similarity * 100)
        dimensions["experience"] = round(experience, 2)

    # —— 加权合成（权重总和=100，按百分比折算）——
    weights = job.get("weights") or {}
    score = 0.0
    for key in DIM_KEYS:
        score += (float(weights.get(key, 0)) / 100.0) * dimensions[key]
    score = round(score, 2)

    reason = output.reason.strip()[:200]
    if vector_degraded:
        reason = (reason + "（" + VECTOR_DEGRADED_NOTE + "）").strip()

    return EngineResult(
        hard_fail=False,
        hard_fail_reason=None,
        score=score,
        dimension_scores={key: round(value, 2) for key, value in dimensions.items()},
        vector_similarity=None if vector_similarity is None else round(vector_similarity, 4),
        vector_degraded=vector_degraded,
        hits=_dedup(llm_hits + hits),
        # 不符项与待核实项仍可见（V2.6 §5），但只是展示项，不是淘汰结论
        misses=_dedup(
            llm_misses + [m for m in misses if m not in llm_misses] + gaps + pending
        ),
        reason=reason,
    )


def _profile_similarity(
    profile: Mapping[str, Any], job_vector: Sequence[float] | None
) -> tuple[float | None, bool]:
    """取画像向量并算与岗位向量的 COSINE；任何不可用 → (None, True) 降级（§4.2）。"""
    if job_vector is None:
        return None, True
    if profile.get("vector_status") != "synced":
        return None, True
    profile_id = profile.get("talent_profile_id")
    if profile_id is None:
        return None, True
    try:
        from app.core.milvus_store import MilvusStoreError, get_profile_vector

        profile_vector = get_profile_vector(int(profile_id))
    except MilvusStoreError as exc:
        logger.warning("画像 %s 向量获取失败，按降级处理：%s", profile_id, exc)
        return None, True
    except Exception as exc:  # noqa: BLE001 - 任何向量侧异常都降级，不阻塞批次
        logger.warning("画像 %s 向量获取异常，按降级处理：%s", profile_id, exc)
        return None, True
    if profile_vector is None:
        return None, True
    similarity = cosine_similarity(job_vector, profile_vector)
    if similarity is None:
        return None, True
    return similarity, False


def _dedup(items: Sequence[str]) -> list[str]:
    """去重保序（hits/misses 合并去重后落 hits_json/misses_json，Prompt C 注）。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


__all__ = [
    "EngineResult",
    "VECTOR_DEGRADED_NOTE",
    "build_candidate_summary",
    "build_job_summary",
    "build_job_vector_text",
    "build_rule_review_text",
    "classify_rule_gaps",
    "cosine_similarity",
    "evaluate",
    "hard_gate",
    "skill_variant_names",
]
