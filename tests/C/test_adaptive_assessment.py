"""自适应测评：策略校验、选题引擎、逐题留痕与能力分析（C，用户 2026-11-07 裁决）。"""

from __future__ import annotations

import unittest

from c_harness import Harness
from app.core.errors import BizError
from app.modules.assessments import adaptive, service
from app.modules.assessments.scoring import parse_dimensions

DIMENSIONS = parse_dimensions(
    [
        {"name": "专业", "type": "professional", "weight": 50},
        {"name": "通用", "type": "general", "weight": 50},
    ]
)


class AdaptiveStrategyTest(unittest.TestCase):
    def test_default_strategy_bounds_follow_total(self):
        strategy = adaptive.default_strategy(4, 2)
        self.assertEqual(strategy["min_question_count"], 4)
        self.assertEqual(strategy["max_question_count"], 4)
        self.assertEqual(strategy["start_difficulty"], "medium")
        self.assertEqual(strategy["rating_bands"][-1]["min_score"], 0.0)

    def test_validate_rejects_bounds_that_cannot_cover_dimensions(self):
        with self.assertRaises(BizError) as ctx:
            adaptive.validate_strategy(
                {"min_question_count": 1, "max_question_count": 4},
                total_question_count=4,
                dimension_count=2,
            )
        self.assertEqual(ctx.exception.code, 400)

    def test_validate_rejects_total_outside_bounds(self):
        with self.assertRaises(BizError):
            adaptive.validate_strategy(
                {"min_question_count": 5, "max_question_count": 6},
                total_question_count=4,
                dimension_count=2,
            )

    def test_validate_rejects_bands_without_zero_floor(self):
        with self.assertRaises(BizError):
            adaptive.validate_strategy(
                {"rating_bands": [{"level": "合格", "min_score": 60}]},
                total_question_count=4,
                dimension_count=2,
            )

    def test_validate_normalizes_band_order(self):
        strategy = adaptive.validate_strategy(
            {
                "rating_bands": [
                    {"level": "不合格", "min_score": 0},
                    {"level": "优秀", "min_score": 90},
                ],
                "start_difficulty": "easy",
            },
            total_question_count=4,
            dimension_count=2,
        )
        self.assertEqual([item["level"] for item in strategy["rating_bands"]], ["优秀", "不合格"])
        self.assertEqual(strategy["start_difficulty"], "easy")


class AdaptiveSelectionTest(unittest.TestCase):
    def _candidates(self):
        items = []
        question_id = 1
        for name in ("专业", "通用"):
            for difficulty in adaptive.DIFFICULTY_ORDER:
                for _ in range(3):
                    items.append(
                        {
                            "question_id": question_id,
                            "dimension_name": name,
                            "dimension_type": "professional",
                            "difficulty": difficulty,
                            "job_id": 1,
                        }
                    )
                    question_id += 1
        return items

    def _strategy(self):
        return adaptive.default_strategy(4, 2)

    def test_first_question_covers_first_dimension_at_start_difficulty(self):
        chosen = adaptive.choose_next_question(
            candidates=self._candidates(),
            asked=[],
            dimensions=DIMENSIONS,
            strategy=self._strategy(),
            target_question_count=4,
            seed=1,
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["dimension_name"], "专业")
        self.assertEqual(chosen["difficulty"], "medium")
        self.assertEqual(chosen["reason"]["rule"], "coverage")

    def test_second_question_covers_remaining_dimension(self):
        chosen = adaptive.choose_next_question(
            candidates=self._candidates(),
            asked=[{"question_id": 2, "dimension_name": "专业", "difficulty": "medium", "score": 100}],
            dimensions=DIMENSIONS,
            strategy=self._strategy(),
            target_question_count=4,
            seed=1,
        )
        self.assertEqual(chosen["dimension_name"], "通用")

    def test_difficulty_rises_after_high_score_and_falls_after_low(self):
        candidates = self._candidates()
        high = adaptive.choose_next_question(
            candidates=candidates,
            asked=[
                {"question_id": 2, "dimension_name": "专业", "difficulty": "medium", "score": 100},
                {"question_id": 5, "dimension_name": "通用", "difficulty": "medium", "score": 100},
            ],
            dimensions=DIMENSIONS,
            strategy=self._strategy(),
            target_question_count=4,
            seed=1,
        )
        self.assertEqual(high["dimension_name"], "专业")
        self.assertEqual(high["difficulty"], "hard")
        low = adaptive.choose_next_question(
            candidates=candidates,
            asked=[
                {"question_id": 3, "dimension_name": "专业", "difficulty": "hard", "score": 20},
                {"question_id": 4, "dimension_name": "通用", "difficulty": "easy", "score": 20},
            ],
            dimensions=DIMENSIONS,
            strategy=self._strategy(),
            target_question_count=4,
            seed=1,
        )
        self.assertEqual(low["dimension_name"], "专业")
        self.assertEqual(low["difficulty"], "medium")

    def test_no_repeat_and_seed_changes_order(self):
        candidates = self._candidates()
        strategy = self._strategy()
        picks_a = []
        picks_b = []
        asked_a: list[dict] = []
        asked_b: list[dict] = []
        for _ in range(3):
            first = adaptive.choose_next_question(
                candidates=candidates,
                asked=list(asked_a),
                dimensions=DIMENSIONS,
                strategy=strategy,
                target_question_count=4,
                seed=7,
            )
            picks_a.append(first["question_id"])
            asked_a.append(
                {
                    "question_id": first["question_id"],
                    "dimension_name": first["dimension_name"],
                    "difficulty": first["difficulty"],
                    "score": None,
                }
            )
            second = adaptive.choose_next_question(
                candidates=candidates,
                asked=list(asked_b),
                dimensions=DIMENSIONS,
                strategy=strategy,
                target_question_count=4,
                seed=99,
            )
            picks_b.append(second["question_id"])
            asked_b.append(
                {
                    "question_id": second["question_id"],
                    "dimension_name": second["dimension_name"],
                    "difficulty": second["difficulty"],
                    "score": None,
                }
            )
        self.assertEqual(len(set(picks_a)), 3)
        self.assertEqual(len(set(picks_b)), 3)
        repeat = adaptive.choose_next_question(
            candidates=candidates,
            asked=list(asked_a),
            dimensions=DIMENSIONS,
            strategy=strategy,
            target_question_count=4,
            seed=7,
        )
        self.assertNoneOrNew(repeat, picks_a)

    def assertNoneOrNew(self, chosen, existing):
        if chosen is not None:
            self.assertNotIn(chosen["question_id"], existing)

    def test_bank_exhausted_returns_none(self):
        chosen = adaptive.choose_next_question(
            candidates=[],
            asked=[],
            dimensions=DIMENSIONS,
            strategy=self._strategy(),
            target_question_count=4,
            seed=1,
        )
        self.assertIsNone(chosen)


class AdaptiveEndingAndAbilityTest(unittest.TestCase):
    def test_ending_rules(self):
        strategy = {**adaptive.default_strategy(4, 2), "max_question_count": 6}
        self.assertIsNone(
            adaptive.ending_reason(
                asked_count=3,
                covered_dimensions=["专业"],
                dimensions=DIMENSIONS,
                strategy=strategy,
                target_question_count=4,
            )
        )
        self.assertIsNone(
            adaptive.ending_reason(
                asked_count=4,
                covered_dimensions=["专业"],
                dimensions=DIMENSIONS,
                strategy=strategy,
                target_question_count=4,
            )
        )
        self.assertEqual(
            adaptive.ending_reason(
                asked_count=4,
                covered_dimensions=["专业", "通用"],
                dimensions=DIMENSIONS,
                strategy=strategy,
                target_question_count=4,
            ),
            "target_question_count",
        )
        self.assertEqual(
            adaptive.ending_reason(
                asked_count=6,
                covered_dimensions=["专业", "通用"],
                dimensions=DIMENSIONS,
                strategy={**strategy, "max_question_count": 6},
                target_question_count=4,
            ),
            "max_question_count",
        )

    def test_ability_radar_rating_and_gaps(self):
        ability = adaptive.build_ability(
            [
                {"name": "专业", "score": 92, "weight": 50, "question_count": 2},
                {"name": "通用", "score": 55, "weight": 50, "question_count": 2},
            ],
            total_score=73.5,
            pass_score=60,
            strategy={"rating_bands": [{"level": "合格", "min_score": 70}, {"level": "不合格", "min_score": 0}]},
        )
        self.assertEqual(ability["radar"]["dimensions"], ["专业", "通用"])
        self.assertEqual(ability["radar"]["scores"], [92.0, 55.0])
        self.assertEqual(ability["rating"]["level"], "合格")
        self.assertEqual(ability["strengths"], ["专业"])
        self.assertEqual(ability["gaps"], ["通用"])
        self.assertIn("总分 73.5", ability["summary"])


class AdaptiveApiFlowTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.admin = self.harness.create_user("admin2", role="admin")
        self.employee = self.harness.create_user("employee2", role="employee")
        self.client = self.harness.client

    def tearDown(self):
        self.harness.close()

    def _post(self, path, payload, user=None):
        return self.client.post(path, json=payload, headers=self.harness.headers(user or self.admin))

    def _approve(self, question_id):
        response = self._post(f"/api/assessments/questions/{question_id}/approve", {})
        self.assertEqual(response.status_code, 200, response.text)

    def _create_questions(self):
        for name, dimension_type in (("专业", "professional"), ("通用", "general")):
            for difficulty in adaptive.DIFFICULTY_ORDER:
                for index in range(2):
                    response = self._post(
                        "/api/assessments/questions",
                        {
                            "dimension_name": name,
                            "dimension_type": dimension_type,
                            "difficulty": difficulty,
                            "question_type": "single_choice",
                            "stem": f"{name}-{difficulty}-{index}",
                            "options": ["A", "B", "C"],
                            "correct_answer": "A",
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self._approve(response.json()["data"]["id"])

    def _create_adaptive_plan(self, total=4, pass_score=60, strategy=None):
        payload = {
            "purpose": "employee",
            "job_id": 1,
            "job_title": "后端工程师",
            "dimensions": [
                {"name": "专业", "type": "professional", "weight": 50},
                {"name": "通用", "type": "general", "weight": 50},
            ],
            "total_question_count": total,
            "pass_score": pass_score,
            "mode": "adaptive",
        }
        if strategy:
            payload["strategy"] = strategy
        return self._post("/api/assessments/plans", payload)

    def _assign(self, plan_id):
        response = self._post(
            "/api/assessments/assignments",
            {"plan_id": plan_id, "user_id": self.employee.id, "idempotency_key": f"adaptive-{plan_id}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_adaptive_publish_freezes_bank_without_paper(self):
        self._create_questions()
        plan = self._create_adaptive_plan()
        self.assertEqual(plan.status_code, 200, plan.text)
        plan_id = plan.json()["data"]["id"]
        published = self._post(f"/api/assessments/plans/{plan_id}/publish", {})
        self.assertEqual(published.status_code, 200, published.text)
        data = published.json()["data"]
        self.assertIsNone(data.get("paper"))
        self.assertEqual(data["mode"], "adaptive")
        self.assertIn("bank_snapshot", data)
        assignment = self._assign(plan_id)
        self.assertEqual(assignment["mode"], "adaptive")
        self.assertIsNone(assignment.get("paper_id"))

    def test_adaptive_rejects_publish_when_bank_insufficient(self):
        self._create_questions()
        plan = self._create_adaptive_plan(total=13)
        plan_id = plan.json()["data"]["id"]
        published = self._post(f"/api/assessments/plans/{plan_id}/publish", {})
        self.assertEqual(published.status_code, 400, published.text)

    def test_adaptive_flow_records_selection_and_ability(self):
        self._create_questions()
        plan = self._create_adaptive_plan(
            strategy={
                "min_question_count": 4,
                "max_question_count": 5,
                "start_difficulty": "medium",
                "rating_bands": [
                    {"level": "良好", "min_score": 80},
                    {"level": "不合格", "min_score": 0},
                ],
            }
        )
        plan_id = plan.json()["data"]["id"]
        self.assertEqual(self._post(f"/api/assessments/plans/{plan_id}/publish", {}).status_code, 200)
        assignment = self._assign(plan_id)
        assignment_id = assignment["id"]

        first = self._next(assignment_id)
        question = first["question"]
        self.assertNotIn("correct_answer", question)
        again = self._next(assignment_id)
        self.assertEqual(again["question"]["question_id"], question["question_id"])

        sequences = []
        data = first
        for _ in range(10):
            if data["done"]:
                break
            current = data["question"]
            sequences.append(
                (data["sequence_no"], current["question_id"], current["dimension_name"], current["difficulty"])
            )
            saved = self.client.put(
                f"/api/assessments/assignments/{assignment_id}/answers/{current['question_id']}",
                json={"selected_option": "A"},
                headers=self.harness.headers(self.employee),
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            data = self._next(assignment_id)

        self.assertEqual(len({item[1] for item in sequences}), len(sequences))
        self.assertEqual([item[0] for item in sequences], list(range(1, len(sequences) + 1)))
        self.assertEqual({item[2] for item in sequences}, {"专业", "通用"})
        self.assertEqual(len(sequences), 4)
        self.assertTrue(data["done"])

        denied = self.client.get(
            f"/api/assessments/assignments/{assignment_id}/selections",
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        admin_trace = self.client.get(
            f"/api/assessments/assignments/{assignment_id}/selections",
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(admin_trace.status_code, 200, admin_trace.text)
        reasons = [item["selection_reason"]["rule"] for item in admin_trace.json()["data"]["items"]]
        self.assertEqual(reasons[0], "coverage")

        submit = self.client.post(
            f"/api/assessments/assignments/{assignment_id}/submit",
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(submit.status_code, 200, submit.text)
        payload = submit.json()["data"]
        self.assertEqual(payload["status"], "completed")
        result = payload["result"]
        self.assertIsNotNone(result)
        self.assertEqual(result["ability"]["radar"]["dimensions"], ["专业", "通用"])
        self.assertIn("rating", result["ability"])
        self.assertEqual(len(result["dimension_scores"]), 2)

    def _next(self, assignment_id):
        response = self._post(
            f"/api/assessments/assignments/{assignment_id}/next-question", {}, user=self.employee
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_adaptive_submit_without_questions_is_rejected(self):
        self._create_questions()
        plan = self._create_adaptive_plan()
        plan_id = plan.json()["data"]["id"]
        self.assertEqual(self._post(f"/api/assessments/plans/{plan_id}/publish", {}).status_code, 200)
        assignment = self._assign(plan_id)
        submit = self.client.post(
            f"/api/assessments/assignments/{assignment['id']}/submit",
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(submit.status_code, 400, submit.text)

    def test_fixed_mode_rejects_next_question(self):
        self._create_questions()
        plan = self._post(
            "/api/assessments/plans",
            {
                "purpose": "employee",
                "job_id": 1,
                "job_title": "后端工程师",
                "dimensions": [
                    {"name": "专业", "type": "professional", "weight": 50},
                    {"name": "通用", "type": "general", "weight": 50},
                ],
                "total_question_count": 2,
                "pass_score": 60,
                "mode": "fixed",
            },
        )
        plan_id = plan.json()["data"]["id"]
        self.assertEqual(self._post(f"/api/assessments/plans/{plan_id}/publish", {}).status_code, 200)
        assignment = self._assign(plan_id)
        response = self._post(
            f"/api/assessments/assignments/{assignment['id']}/next-question", {}, user=self.employee
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_generation_endpoint_uses_model_and_keeps_pending(self):
        original = service.default_generate_questions
        service.default_generate_questions = lambda request: [
            {
                "stem": "生成题：缓存穿透是什么",
                "options": ["A", "B", "C"],
                "correct_answer": "A",
                "reference_points": None,
                "scoring_guideline": None,
            }
        ]
        try:
            response = self._post(
                "/api/assessments/questions/generate-draft",
                {"dimension_name": "专业", "difficulty": "medium", "count": 1},
            )
        finally:
            service.default_generate_questions = original
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["data"][0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["stem"], "生成题：缓存穿透是什么")

    def test_generation_failure_is_503_without_writes(self):
        original = service.default_generate_questions

        def boom(request):
            raise RuntimeError("模型不可用")

        service.default_generate_questions = boom
        try:
            response = self._post(
                "/api/assessments/questions/generate-draft",
                {"dimension_name": "专业", "difficulty": "medium", "count": 1},
            )
        finally:
            service.default_generate_questions = original
        self.assertEqual(response.status_code, 503, response.text)
        listing = self.client.get(
            "/api/assessments/questions", headers=self.harness.headers(self.admin)
        )
        self.assertEqual(listing.json()["data"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
