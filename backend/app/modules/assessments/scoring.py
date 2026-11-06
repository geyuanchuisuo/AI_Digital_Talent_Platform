"""固定测评的纯计算规则（契约 §3）。

- 各维度题量：先每维度 1 题，剩余按权重最大余数法分配（一期简化，C-Q10）。
- 难度结构：按方案冻结的百分比分配每维度各难度题数；默认全 medium。
- 计分：单选精确匹配 0/100；简答由真实模型给出 0–100；维度分=题均分；
  总分=Σ(维度分×权重)/100，按未舍入总分与总分线比较（含相等通过）。
本文件不读写数据库，便于单测直接覆盖临界值。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.core.errors import BizError

DEFAULT_DIFFICULTY_STRUCTURE: dict[str, float] = {"easy": 0.0, "medium": 100.0, "hard": 0.0}
DIFFICULTY_ORDER = ("easy", "medium", "hard")
WEIGHT_TOTAL_TOLERANCE = 0.01


@dataclass(frozen=True)
class Dimension:
    name: str
    type: str
    weight: float


def parse_dimensions(raw: list[dict[str, Any]]) -> list[Dimension]:
    """请求/库内 JSON → 维度列表；重名校验在发布前统一做。"""
    return [
        Dimension(
            name=str(item.get("name", "")).strip(),
            type=str(item.get("type", "professional")),
            weight=float(item.get("weight", 0)),
        )
        for item in raw
    ]


def validate_plan_input(
    dimensions: list[Dimension],
    total_question_count: int,
    pass_score: float,
    difficulty_structure: dict[str, float],
) -> None:
    """发布前的结构校验，非法统一 400（契约 §3.1）。"""
    if not dimensions:
        raise BizError(400, "至少选择一个考核维度")
    names = [item.name for item in dimensions]
    if any(not name for name in names):
        raise BizError(400, "维度名称不能为空")
    if len(set(names)) != len(names):
        raise BizError(400, "考核维度名称不能重复")
    weight_total = round(sum(item.weight for item in dimensions), 2)
    if abs(weight_total - 100) > WEIGHT_TOTAL_TOLERANCE:
        raise BizError(400, f"维度权重合计必须为 100，当前为 {weight_total}")
    if any(item.weight <= 0 for item in dimensions):
        raise BizError(400, "每个维度权重必须大于 0")
    if total_question_count < len(dimensions):
        raise BizError(
            400,
            f"总题量 {total_question_count} 少于维度数 {len(dimensions)}，无法覆盖全部考核维度，请调整题量或维度",
        )
    if not (0 <= float(pass_score) <= 100):
        raise BizError(400, "总分线必须在 0–100 之间")
    validate_difficulty_structure(difficulty_structure)


def validate_difficulty_structure(structure: dict[str, float]) -> dict[str, float]:
    """难度结构：只允许 easy/medium/hard，合计 100；缺省为全部 medium。"""
    if not structure:
        return dict(DEFAULT_DIFFICULTY_STRUCTURE)
    unknown = set(structure) - set(DIFFICULTY_ORDER)
    if unknown:
        raise BizError(400, f"难度结构包含未知难度：{sorted(unknown)}")
    values = {key: float(structure.get(key, 0)) for key in DIFFICULTY_ORDER}
    if any(value < 0 for value in values.values()):
        raise BizError(400, "难度结构比例不能为负数")
    total = round(sum(values.values()), 2)
    if abs(total - 100) > WEIGHT_TOTAL_TOLERANCE:
        raise BizError(400, f"难度结构比例合计必须为 100，当前为 {total}")
    return values


def _max_remainder(total: int, weights: list[float]) -> list[int]:
    """按权重把 total 分解为整数份额；最大余数法、余数相同按原顺序。"""
    if total <= 0:
        return [0 for _ in weights]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise BizError(400, "权重合计必须大于 0")
    exact = [total * (weight / weight_sum) for weight in weights]
    floors = [int(value) for value in exact]
    remainders = [exact[i] - floors[i] for i in range(len(exact))]
    left = total - sum(floors)
    order = sorted(range(len(exact)), key=lambda i: (-remainders[i], i))
    for index in order[:left]:
        floors[index] += 1
    return floors


def allocate_question_counts(dimensions: list[Dimension], total_question_count: int) -> list[int]:
    """每维度至少 1 题，剩余按权重分配；保证总和等于总题量。"""
    if total_question_count < len(dimensions):
        raise BizError(
            400,
            f"总题量 {total_question_count} 少于维度数 {len(dimensions)}，无法覆盖全部考核维度",
        )
    base = [1 for _ in dimensions]
    remaining = total_question_count - len(dimensions)
    extra = _max_remainder(remaining, [item.weight for item in dimensions])
    return [base[i] + extra[i] for i in range(len(dimensions))]


def allocate_difficulty_slots(count: int, structure: dict[str, float]) -> dict[str, int]:
    """单维度 count 题按难度结构拆分为各难度题数（保证合计 count）。"""
    structure = structure or DEFAULT_DIFFICULTY_STRUCTURE
    weights = [float(structure.get(key, 0)) for key in DIFFICULTY_ORDER]
    shares = _max_remainder(count, weights) if sum(weights) > 0 else [0, count, 0]
    return dict(zip(DIFFICULTY_ORDER, shares, strict=True))


def grade_single_choice(correct_answer: str | None, selected_option: str | None) -> tuple[bool, float]:
    """单选精确匹配（去首尾空白、大小写敏感，契约 §3.4）。"""
    correct = (correct_answer or "").strip()
    selected = (selected_option or "").strip()
    is_correct = bool(correct) and selected == correct
    return is_correct, 100.0 if is_correct else 0.0


def _round2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_result(
    dimensions: list[Dimension],
    answers: list[dict[str, Any]],
    pass_score: float,
) -> dict[str, Any]:
    """answers 每项需含 `dimension_name` 与 `score`；返回维度分、总分与通过结论。"""
    by_dimension: dict[str, list[float]] = {item.name: [] for item in dimensions}
    for answer in answers:
        name = str(answer.get("dimension_name", ""))
        if name not in by_dimension:
            raise BizError(500, f"答案维度 {name} 不属于当前试卷")
        by_dimension[name].append(float(answer.get("score") or 0))

    dimension_scores: list[dict[str, Any]] = []
    weighted_sum = 0.0
    for item in dimensions:
        scores = by_dimension[item.name]
        average = sum(scores) / len(scores) if scores else 0.0
        weighted_sum += average * item.weight
        dimension_scores.append(
            {
                "name": item.name,
                "type": item.type,
                "weight": item.weight,
                "question_count": len(scores),
                "score": _round2(average),
            }
        )
    total = weighted_sum / 100.0
    return {
        "dimension_scores": dimension_scores,
        "total_score": _round2(total),
        "total_score_raw": total,
        "pass_score": float(pass_score),
        "passed": total >= float(pass_score),
    }
