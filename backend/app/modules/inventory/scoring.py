"""F10 人才盘点纯计算：九宫格、可见性裁剪、部门匿名汇总 <5 隐藏。"""

from __future__ import annotations

from typing import Any

from app.core.errors import BizError

PERFORMANCE_LABELS = {1: "需改进", 2: "待发展", 3: "合格", 4: "良好", 5: "优秀"}
POTENTIAL_LABELS = {1: "低", 2: "中低", 3: "中", 4: "中高", 5: "高"}
GRID_LABELS = {
    "hi-hi": "明星",
    "hi-mid": "核心骨干",
    "hi-lo": "专业深耕",
    "mid-hi": "高潜培养",
    "mid-mid": "稳定贡献",
    "mid-lo": "观察发展",
    "lo-hi": "潜力待激活",
    "lo-mid": "调整改进",
    "lo-lo": "需重点关注",
}


def _tier(score: int) -> str:
    if score >= 4:
        return "hi"
    if score == 3:
        return "mid"
    return "lo"


def grid_cell(performance: int, potential: int) -> str:
    """绩效 × 潜力 → 九宫格 cell id；输入需 1–5。"""
    if not (1 <= performance <= 5 and 1 <= potential <= 5):
        raise BizError(400, "绩效与潜力评分必须在 1–5 之间")
    return f"{_tier(performance)}-{_tier(potential)}"


def grid_label(cell: str) -> str:
    return GRID_LABELS.get(cell, "未知")


def public_safe_review(review: dict[str, Any], *, viewer_role: str) -> dict[str, Any]:
    """员工本人只看到公开评估（不返回内部评级/九宫格/人才池）。"""
    result = dict(review)
    if viewer_role not in ("admin", "hr"):
        for key in ("performance_score", "potential_manager_score", "potential_hr_score", "grid_cell"):
            result.pop(key, None)
    return result


def visible_summary(rows: list[dict[str, Any]], *, viewer_role: str) -> dict[str, Any]:
    """部门匿名汇总：cell 计数 <5 的档位合并为"其他"，永不返回个人身份。"""
    if viewer_role not in ("admin", "hr"):
        raise BizError(403, "仅管理员可查看汇总")
    counts: dict[str, int] = {}
    for row in rows:
        cell = row.get("grid_cell")
        if not cell:
            continue
        counts[cell] = counts.get(cell, 0) + 1
    visible: dict[str, int] = {}
    hidden_count = 0
    for cell, count in counts.items():
        if count < 5:
            hidden_count += count
        else:
            visible[cell] = count
    if hidden_count:
        visible["_under_threshold"] = hidden_count
    return {"total": sum(counts.values()), "cells": visible, "labels": {c: grid_label(c) for c in visible}}
