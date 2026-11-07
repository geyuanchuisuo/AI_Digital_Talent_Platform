"""自适应测评策略、选题与能力分析（C 契约 §3；用户 2026-11-07 裁决）。

口径：
- 方案发布时冻结策略（题量边界、起始难度、升降难度、评级档位）与题库快照；
  不提前冻结每个考生的全部题目（千人千卷）。
- 选题先覆盖未作答维度，再按权重配额缺口选维度；难度按该维度上一题得分升降
  （得分 ≥ ADVANCE_SCORE 升一档，否则降一档），无历史得分时用方案起始难度。
- 同难度候选按「同岗位优先 + 任务种子稳定哈希」排序：同一任务可复现，不同任务题目分散。
- 结束规则：达到 max 强制结束；未达 min 继续；未覆盖全部维度继续；达到 target 结束。
- 能力分析：维度分 → 雷达数据、档位与评级；评级档位可由方案覆盖。

本模块不读写数据库，便于单测直接覆盖边界。
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.errors import BizError
from app.modules.assessments.scoring import allocate_question_counts

DIFFICULTY_ORDER = ("easy", "medium", "hard")
ADVANCE_SCORE = 70.0
MAX_QUESTION_COUNT = 200

DEFAULT_RATING_BANDS: tuple[dict[str, Any], ...] = (
    {"level": "优秀", "min_score": 90.0},
    {"level": "良好", "min_score": 80.0},
    {"level": "合格", "min_score": 70.0},
    {"level": "待提升", "min_score": 60.0},
    {"level": "不合格", "min_score": 0.0},
)


def _as_int(raw: Any, field: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BizError(400, f"自适应策略字段 {field} 必须是整数") from exc


def _normalize_bands(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return [dict(item) for item in DEFAULT_RATING_BANDS]
    bands: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise BizError(400, "评级档位格式不正确，应为 {level, min_score} 列表")
        level = str(item.get("level", "")).strip()
        if not level:
            raise BizError(400, "评级档位名称不能为空")
        try:
            min_score = float(item.get("min_score"))
        except (TypeError, ValueError) as exc:
            raise BizError(400, f"评级档位「{level}」的最低分必须是数字") from exc
        if not 0 <= min_score <= 100:
            raise BizError(400, f"评级档位「{level}」的最低分必须在 0–100 之间")
        bands.append({"level": level, "min_score": round(min_score, 2)})
    if len({item["level"] for item in bands}) != len(bands):
        raise BizError(400, "评级档位名称不能重复")
    bands.sort(key=lambda item: item["min_score"], reverse=True)
    if bands[-1]["min_score"] > 0:
        raise BizError(400, "评级档位必须包含 0 分档，否则低分无法评级")
    return bands


def default_strategy(total_question_count: int, dimension_count: int) -> dict[str, Any]:
    """默认策略：题量固定为方案总题量、起始 medium、按表现升降、默认评级档位。"""
    total = max(int(total_question_count), int(dimension_count), 1)
    return {
        "min_question_count": total,
        "max_question_count": total,
        "start_difficulty": "medium",
        "adjust_difficulty": True,
        "rating_bands": [dict(item) for item in DEFAULT_RATING_BANDS],
    }


def validate_strategy(
    raw: dict[str, Any] | None,
    *,
    total_question_count: int,
    dimension_count: int,
) -> dict[str, Any]:
    """发布前冻结策略；边界必须保证能覆盖全部维度且不超题库上限。"""
    if not raw:
        return default_strategy(total_question_count, dimension_count)
    total = int(total_question_count)
    min_count = _as_int(raw.get("min_question_count", total), "min_question_count")
    max_count = _as_int(raw.get("max_question_count", total), "max_question_count")
    if min_count < dimension_count:
        raise BizError(
            400,
            f"最少题量不能少于维度数 {dimension_count}，否则无法覆盖全部考核维度",
        )
    if not (min_count <= total <= max_count):
        raise BizError(400, "题量边界必须满足：最少题量 ≤ 方案总题量 ≤ 最多题量")
    if max_count > MAX_QUESTION_COUNT:
        raise BizError(400, f"最多题量不能超过 {MAX_QUESTION_COUNT}")
    start = str(raw.get("start_difficulty") or "medium")
    if start not in DIFFICULTY_ORDER:
        raise BizError(400, f"起始难度必须是 {list(DIFFICULTY_ORDER)}")
    adjust = raw.get("adjust_difficulty", True)
    if not isinstance(adjust, bool):
        raise BizError(400, "adjust_difficulty 必须是布尔值")
    return {
        "min_question_count": min_count,
        "max_question_count": max_count,
        "start_difficulty": start,
        "adjust_difficulty": adjust,
        "rating_bands": _normalize_bands(raw.get("rating_bands")),
    }


def ending_reason(
    *,
    asked_count: int,
    covered_dimensions: list[str],
    dimensions: list[Any],
    strategy: dict[str, Any],
    target_question_count: int,
) -> str | None:
    """返回结束原因；None 表示还需继续出题。"""
    max_count = _as_int(strategy.get("max_question_count") or target_question_count, "max_question_count")
    min_count = _as_int(strategy.get("min_question_count") or target_question_count, "min_question_count")
    if asked_count >= max_count:
        return "max_question_count"
    if asked_count < min_count:
        return None
    missing = [item.name for item in dimensions if item.name not in covered_dimensions]
    if missing:
        return None
    if asked_count >= int(target_question_count):
        return "target_question_count"
    return None


def _difficulty_index(value: Any) -> int:
    return DIFFICULTY_ORDER.index(value) if value in DIFFICULTY_ORDER else 1


def _next_difficulty(strategy: dict[str, Any], last: dict[str, Any] | None) -> str:
    start = str(strategy.get("start_difficulty") or "medium")
    if not strategy.get("adjust_difficulty", True) or last is None:
        return start
    score = last.get("score")
    if score is None:
        return start
    index = _difficulty_index(last.get("difficulty") or start)
    if float(score) >= ADVANCE_SCORE:
        index = min(index + 1, len(DIFFICULTY_ORDER) - 1)
    else:
        index = max(index - 1, 0)
    return DIFFICULTY_ORDER[index]


def _stable_key(seed: int, question_id: int) -> str:
    return hashlib.sha256(f"{seed}:{question_id}".encode("utf-8")).hexdigest()


def choose_next_question(
    *,
    candidates: list[dict[str, Any]],
    asked: list[dict[str, Any]],
    dimensions: list[Any],
    strategy: dict[str, Any],
    target_question_count: int,
    seed: int,
    plan_job_id: int | None = None,
) -> dict[str, Any] | None:
    """按策略选下一题；候选耗尽返回 None。

    `asked` 按出题顺序排列，每项含 question_id/dimension_name/difficulty/score；
    score 为 None 表示该题尚未评分（仅简答未交卷时出现）。
    """
    names = [item.name for item in dimensions]
    asked_ids = {int(item["question_id"]) for item in asked}
    pool = [
        item
        for item in candidates
        if int(item["question_id"]) not in asked_ids and item.get("dimension_name") in names
    ]
    if not pool:
        return None

    quota = allocate_question_counts(dimensions, int(target_question_count))
    asked_by_dim = {name: 0 for name in names}
    last_by_dim: dict[str, dict[str, Any]] = {}
    for item in asked:
        name = item.get("dimension_name")
        if name in asked_by_dim:
            asked_by_dim[name] += 1
            last_by_dim[name] = item
    deficits = {
        names[index]: quota[index] - asked_by_dim[names[index]] for index in range(len(names))
    }

    uncovered = [name for name in names if asked_by_dim[name] == 0]
    if uncovered:
        rule = "coverage"
        target_dimension = uncovered[0]
    else:
        rule = "quota"
        target_dimension = max(names, key=lambda name: (deficits[name], -names.index(name)))
        if deficits[target_dimension] <= 0:
            target_dimension = names[0]

    dimension_pool = [item for item in pool if item.get("dimension_name") == target_dimension]
    if not dimension_pool:
        rule = "fallback"
        for name in names:
            if name == target_dimension:
                continue
            alternative = [item for item in pool if item.get("dimension_name") == name]
            if alternative and (deficits[name] > 0 or asked_by_dim[name] == 0):
                target_dimension, dimension_pool = name, alternative
                break
        if not dimension_pool:
            return None

    desired = _next_difficulty(strategy, last_by_dim.get(target_dimension))
    preferred = [item for item in dimension_pool if item.get("difficulty") == desired]
    if preferred:
        difficulty_rule = "desired"
        chosen_pool = preferred
    else:
        desired_index = _difficulty_index(desired)
        distance = min(abs(_difficulty_index(item.get("difficulty")) - desired_index) for item in dimension_pool)
        difficulty_rule = "nearest"
        chosen_pool = [
            item
            for item in dimension_pool
            if abs(_difficulty_index(item.get("difficulty")) - desired_index) == distance
        ]

    ordered = sorted(
        chosen_pool,
        key=lambda item: (
            0 if (plan_job_id is not None and item.get("job_id") == plan_job_id) else 1,
            _stable_key(seed, int(item["question_id"])),
            int(item["question_id"]),
        ),
    )
    chosen = ordered[0]
    last = last_by_dim.get(target_dimension)
    return {
        "question_id": int(chosen["question_id"]),
        "dimension_name": chosen.get("dimension_name"),
        "difficulty": chosen.get("difficulty"),
        "reason": {
            "rule": rule,
            "dimension_gap": deficits.get(target_dimension),
            "asked_count": len(asked),
            "target_question_count": int(target_question_count),
            "desired_difficulty": desired,
            "difficulty_rule": difficulty_rule,
            "last_score": None if last is None else last.get("score"),
        },
    }


def rating_for(score: float, bands: list[dict[str, Any]]) -> dict[str, Any]:
    """按档位取评级；档位按最低分降序且必须覆盖 0 分。"""
    value = float(score)
    for item in bands:
        if value >= float(item["min_score"]):
            return dict(item)
    return dict(bands[-1])


def build_ability(
    dimension_scores: list[dict[str, Any]],
    *,
    total_score: float,
    pass_score: float,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """能力结构分析：雷达数据、维度档位、强项/短板与总分评级。"""
    bands = list((strategy or {}).get("rating_bands") or DEFAULT_RATING_BANDS)
    dimensions = [
        {
            "name": str(item.get("name", "")),
            "score": round(float(item.get("score") or 0), 2),
            "weight": float(item.get("weight") or 0),
            "question_count": int(item.get("question_count") or 0),
        }
        for item in dimension_scores
    ]
    levels = [
        {**item, "level": rating_for(item["score"], bands)["level"]} for item in dimensions
    ]
    ranked = sorted(dimensions, key=lambda item: (-item["score"], item["name"]))
    strengths = [item["name"] for item in ranked if item["score"] >= 80][:2]
    gaps = [
        item["name"]
        for item in sorted(dimensions, key=lambda item: (item["score"], item["name"]))
        if item["score"] < 70
    ][:2]
    rating = rating_for(total_score, bands)
    parts = [f"总分 {round(float(total_score), 2)}，评级「{rating['level']}」"]
    if strengths:
        parts.append("强项：" + "、".join(strengths))
    if gaps:
        parts.append("待提升：" + "、".join(gaps))
    return {
        "radar": {
            "dimensions": [item["name"] for item in dimensions],
            "scores": [item["score"] for item in dimensions],
            "max_score": 100.0,
        },
        "dimension_levels": levels,
        "strengths": strengths,
        "gaps": gaps,
        "rating": {
            "level": rating["level"],
            "min_score": rating["min_score"],
            "total_score": round(float(total_score), 2),
            "pass_score": float(pass_score),
        },
        "summary": "；".join(parts),
    }
