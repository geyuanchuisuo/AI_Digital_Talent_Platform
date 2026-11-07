"""F10 盘点纯计算验证（设计先行，归属生效后迁入 backend/app/modules/inventory/scoring.py）。

验证两条核心规则的算法正确性，不依赖 ORM/路由：
1. 九宫格映射与“待评估”边界（REQ-INV-002 AC-1）。
2. 匿名汇总样本 <5 隐藏（D-30）。
"""

from __future__ import annotations

import unittest

# ---- 设计中的纯函数（实现期迁入 inventory/scoring.py，签名保持不变）----

_LEVELS = ("low", "medium", "high")
# 行=绩效(低→高)，列=潜力(低→高)；1..9 为九宫格格号。
_GRID = {
    ("low", "low"): 1, ("low", "medium"): 2, ("low", "high"): 3,
    ("medium", "low"): 4, ("medium", "medium"): 5, ("medium", "high"): 6,
    ("high", "low"): 7, ("high", "medium"): 8, ("high", "high"): 9,
}


def grid_cell(performance: str | None, potential: str | None) -> int | None:
    """任一缺失/未确认 → 待评估(None)，不推断填补。"""
    if performance not in _LEVELS or potential not in _LEVELS:
        return None
    return _GRID[(performance, potential)]


def visible_summary(values: list[float], min_sample: int = 5) -> float | None:
    """样本不足返回 None（隐藏该项），否则返回均值。"""
    if len(values) < min_sample:
        return None
    return sum(values) / len(values)


class TestInventoryScoring(unittest.TestCase):
    def test_grid_full_mapping(self):
        self.assertEqual(grid_cell("high", "high"), 9)
        self.assertEqual(grid_cell("low", "low"), 1)
        self.assertEqual(grid_cell("medium", "high"), 6)

    def test_grid_pending_when_missing(self):
        self.assertIsNone(grid_cell(None, "high"))
        self.assertIsNone(grid_cell("high", None))
        self.assertIsNone(grid_cell(None, None))

    def test_grid_rejects_unknown_level(self):
        self.assertIsNone(grid_cell("excellent", "high"))

    def test_summary_hidden_below_threshold(self):
        self.assertIsNone(visible_summary([1.0, 2.0, 3.0, 4.0]))  # 4 < 5

    def test_summary_visible_at_threshold(self):
        self.assertEqual(visible_summary([2.0] * 5), 2.0)


if __name__ == "__main__":
    unittest.main()
