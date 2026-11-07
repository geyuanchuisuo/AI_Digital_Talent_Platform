"""F10 盘点纯计算验证（已迁入 backend/app/modules/inventory/scoring.py）。

验证：九宫格映射、非法分拒绝、匿名汇总样本 <5 隐藏、员工视图裁剪。
"""

from __future__ import annotations

import unittest

from app.modules.inventory.scoring import grid_cell, public_safe_review, visible_summary


class TestInventoryScoring(unittest.TestCase):
    def test_grid_full_mapping(self):
        self.assertEqual(grid_cell(5, 5), "hi-hi")
        self.assertEqual(grid_cell(1, 1), "lo-lo")
        self.assertEqual(grid_cell(3, 5), "mid-hi")
        self.assertEqual(grid_cell(5, 1), "hi-lo")

    def test_grid_rejects_out_of_range(self):
        from app.core.errors import BizError

        with self.assertRaises(BizError):
            grid_cell(0, 3)
        with self.assertRaises(BizError):
            grid_cell(6, 3)

    def test_summary_hidden_below_threshold(self):
        rows = [{"grid_cell": "hi-hi"} for _ in range(4)]
        summary = visible_summary(rows, viewer_role="hr")
        self.assertEqual(summary["cells"].get("_under_threshold"), 4)
        self.assertNotIn("hi-hi", summary["cells"])

    def test_summary_visible_at_threshold(self):
        rows = [{"grid_cell": "mid-mid"} for _ in range(5)]
        summary = visible_summary(rows, viewer_role="hr")
        self.assertEqual(summary["cells"]["mid-mid"], 5)

    def test_summary_forbidden_for_non_admin(self):
        from app.core.errors import BizError

        with self.assertRaises(BizError):
            visible_summary([], viewer_role="employee")

    def test_public_safe_review_hides_internal_for_employee(self):
        review = {
            "performance_score": 5,
            "potential_hr_score": 4,
            "grid_cell": "hi-hi",
            "summary": "OK",
        }
        safe = public_safe_review(review, viewer_role="employee")
        self.assertNotIn("performance_score", safe)
        self.assertNotIn("grid_cell", safe)
        self.assertEqual(safe["summary"], "OK")


if __name__ == "__main__":
    unittest.main()
