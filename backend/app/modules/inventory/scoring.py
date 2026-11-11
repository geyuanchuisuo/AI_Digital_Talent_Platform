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


def tier_from_bands(score: int, bands: dict[str, int]) -> str:
    """按经批准的阈值把 1–5 分映射为 hi/mid/lo 档。"""
    hi = bands.get("hi_min")
    mid = bands.get("mid_min")
    if hi is None or mid is None:
        raise BizError(400, "九宫格标准配置缺少 hi_min/mid_min 阈值")
    if not (1 <= score <= 5):
        raise BizError(400, "绩效与潜力评分必须在 1–5 之间")
    if score >= hi:
        return "hi"
    if score >= mid:
        return "mid"
    return "lo"


def grid_cell(performance: int, potential: int, bands: dict[str, dict[str, int]] | None = None) -> str:
    """绩效 × 潜力 → 九宫格 cell id；输入需 1–5。

    `bands` 为经 HR 批准的九宫格标准配置阈值 `{"performance": {...}, "potential": {...}}`；
    缺配置（None）时不套示意数值，正式评级因缺标准而被拒绝。
    """
    if not (1 <= performance <= 5 and 1 <= potential <= 5):
        raise BizError(400, "绩效与潜力评分必须在 1–5 之间")
    if bands is None:
        raise BizError(400, "缺少经 HR 批准的九宫格标准配置，不能产出正式评级")
    perf = tier_from_bands(performance, bands["performance"])
    pot = tier_from_bands(potential, bands["potential"])
    return f"{perf}-{pot}"


def grid_label(cell: str) -> str:
    return GRID_LABELS.get(cell, "未知")


def public_safe_review(review: dict[str, Any], *, viewer_role: str) -> dict[str, Any]:
    """员工本人只看到公开评估（不返回内部评级/九宫格/人才池）。

    部门经理（dept_manager）按 F10 权限矩阵可读本部门当前评级/九宫格，
    行级部门范围由服务层裁剪，这里只做字段级裁剪。
    """
    result = dict(review)
    if viewer_role not in ("admin", "hr", "dept_manager"):
        for key in ("performance_score", "potential_manager_score", "potential_hr_score", "grid_cell"):
            result.pop(key, None)
    return result


def visible_summary(rows: list[dict[str, Any]], *, viewer_role: str) -> dict[str, Any]:
    """部门匿名汇总：cell 计数 <5 的档位合并为"其他"，永不返回个人身份。

    部门经理可看本部门汇总（行集由服务层按负责部门预过滤）。
    """
    if viewer_role not in ("admin", "hr", "dept_manager"):
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
