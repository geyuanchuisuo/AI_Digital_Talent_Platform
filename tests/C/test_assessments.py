"""C 固定测评：组卷/计分/公开/修分/失败重试（契约 §3/§4）。"""

from __future__ import annotations

import unittest

from c_harness import Harness
from app.modules.assessments import service
from app.modules.assessments.scoring import (
    allocate_question_counts,
    compute_result,
    parse_dimensions,
)


class ScoringRulesTest(unittest.TestCase):
    """纯规则：权重最大余数法 + 总分线与维度均分。"""

    def test_allocate_counts_keeps_one_each_and_weight_remainder(self):
        dimensions = parse_dimensions(
            [
                {"name": "专业", "type": "professional", "weight": 50},
                {"name": "通用", "type": "general", "weight": 30},
                {"name": "文化", "type": "general", "weight": 20},
            ]
        )
        counts = allocate_question_counts(dimensions, 5)
        self.assertEqual(counts, [2, 2, 1])
        self.assertEqual(sum(counts), 5)

    def test_total_score_uses_unrounded_value_and_passes_on_equal_line(self):
        dimensions = parse_dimensions([{"name": "专业", "weight": 100, "type": "professional"}])
        # 单选 0/100 下 2 对 3 题 → 66.666...，总分线 66.67 应不通过
        answers = [
            {"dimension_name": "专业", "score": 100},
            {"dimension_name": "专业", "score": 100},
            {"dimension_name": "专业", "score": 0},
        ]
        result = compute_result(dimensions, answers, 66.67)
        self.assertFalse(result["passed"])
        equal_line = compute_result(dimensions, answers, 66.66)
        self.assertTrue(equal_line["passed"])


class AssessmentFlowTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.admin = self.harness.create_user("admin1", role="admin")
        self.candidate = self.harness.create_user("candidate1", role="candidate")
        self.employee = self.harness.create_user("employee1", role="employee")
        self.client = self.harness.client

    def tearDown(self):
        self.harness.close()

    def _post(self, path, payload, user=None):
        return self.client.post(path, json=payload, headers=self.harness.headers(user or self.admin))

    def _create_question(self, dimension, qtype="single_choice", dimension_type="professional"):
        payload = {
            "dimension_name": dimension,
            "dimension_type": dimension_type,
            "difficulty": "medium",
            "question_type": qtype,
            "stem": f"{dimension}-{qtype}-题干",
        }
        if qtype == "single_choice":
            payload.update({"options": ["A", "B", "C"], "correct_answer": "A"})
        else:
            payload.update(
                {"reference_points": "要点一；要点二", "scoring_guideline": "按要点完整度给分"}
            )
        response = self._post("/api/assessments/questions", payload)
        self.assertEqual(response.status_code, 200, response.text)
        question_id = response.json()["data"]["id"]
        approved = self._post(f"/api/assessments/questions/{question_id}/approve", {})
        self.assertEqual(approved.status_code, 200, approved.text)
        return question_id

    def _create_plan(self, purpose="recruitment", total=4, pass_score=60):
        return self._post(
            "/api/assessments/plans",
            {
                "purpose": purpose,
                "job_id": 1,
                "job_title": "后端工程师",
                "dimensions": [
                    {"name": "专业", "type": "professional", "weight": 50},
                    {"name": "通用", "type": "general", "weight": 50},
                ],
                "total_question_count": total,
                "pass_score": pass_score,
                "mode": "fixed",
            },
        )

    def _assign(self, plan_id, user):
        response = self._post(
            "/api/assessments/assignments",
            {"plan_id": plan_id, "user_id": user.id, "idempotency_key": f"k-{user.id}-{plan_id}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def test_publish_rejects_when_question_bank_insufficient(self):
        self._create_question("专业")
        self._create_question("通用", dimension_type="general")
        plan = self._create_plan(total=4)
        self.assertEqual(plan.status_code, 200, plan.text)
        publish = self._post(f"/api/assessments/plans/{plan.json()['data']['id']}/publish", {})
        self.assertEqual(publish.status_code, 400)
        self.assertIn("题库不足", publish.json()["message"])

    def test_weight_total_must_be_100(self):
        response = self._post(
            "/api/assessments/plans",
            {
                "purpose": "recruitment",
                "job_id": 1,
                "job_title": "岗位",
                "dimensions": [{"name": "专业", "weight": 60}],
                "total_question_count": 2,
                "pass_score": 60,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("权重", response.json()["message"])

    def test_recruitment_flow_publish_open_answers_and_revision(self):
        for dimension, dtype in (("专业", "professional"), ("通用", "general")):
            for _ in range(2):
                self._create_question(dimension, dimension_type=dtype)
        plan = self._create_plan()
        plan_id = plan.json()["data"]["id"]
        publish = self._post(f"/api/assessments/plans/{plan_id}/publish", {})
        self.assertEqual(publish.status_code, 200, publish.text)
        self.assertEqual(publish.json()["data"]["paper"]["question_count"], 4)

        assignment_id = self._assign(plan_id, self.candidate)
        # 幂等键重复分配返回同一条任务
        again = self._post(
            "/api/assessments/assignments",
            {"plan_id": plan_id, "user_id": self.candidate.id, "idempotency_key": f"k-{self.candidate.id}-{plan_id}"},
        )
        self.assertEqual(again.json()["data"]["id"], assignment_id)

        start = self.client.post(
            f"/api/assessments/assignments/{assignment_id}/start",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(start.status_code, 200)

        detail = self.client.get(
            f"/api/assessments/assignments/{assignment_id}",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(detail.status_code, 200)
        questions = detail.json()["data"]["questions"]
        self.assertEqual(len(questions), 4)
        self.assertNotIn("correct_answer", questions[0])

        for index, question in enumerate(questions):
            answer = "B" if index == 0 else "A"  # 第一题故意答错
            saved = self.client.put(
                f"/api/assessments/assignments/{assignment_id}/answers/{question['question_id']}",
                json={"selected_option": answer},
                headers=self.harness.headers(self.candidate),
            )
            self.assertEqual(saved.status_code, 200, saved.text)

        submit = self.client.post(
            f"/api/assessments/assignments/{assignment_id}/submit",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(submit.status_code, 200, submit.text)
        self.assertEqual(submit.json()["data"]["status"], "completed")
        # 招聘成绩未公布：候选人交卷响应也不能提前拿到分数
        self.assertIsNone(submit.json()["data"]["result"])

        hidden = self.client.get(
            f"/api/assessments/assignments/{assignment_id}/result",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(hidden.status_code, 403)

        publish_result = self._post(f"/api/assessments/assignments/{assignment_id}/publish", {})
        self.assertEqual(publish_result.status_code, 200)
        visible = self.client.get(
            f"/api/assessments/assignments/{assignment_id}/result",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(visible.status_code, 200)
        result = visible.json()["data"]
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["total_score"], 75.0)
        self.assertTrue(result["passed"])
        self.assertFalse(result["answers_open"])

        reopened = self.client.get(
            f"/api/assessments/assignments/{assignment_id}",
            headers=self.harness.headers(self.candidate),
        )
        self.assertNotIn("correct_answer", reopened.json()["data"]["questions"][0])
        self.assertIsNotNone(reopened.json()["data"]["result"])

        opened = self._post(f"/api/assessments/assignments/{assignment_id}/open-answers", {})
        self.assertEqual(opened.status_code, 200)
        after_open = self.client.get(
            f"/api/assessments/assignments/{assignment_id}",
            headers=self.harness.headers(self.candidate),
        )
        self.assertIn("correct_answer", after_open.json()["data"]["questions"][0])
        opened_result = self.client.get(
            f"/api/assessments/assignments/{assignment_id}/result",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(opened_result.status_code, 200)
        review_items = opened_result.json()["data"]["review_items"]
        self.assertEqual(len(review_items), 1)
        complete = self.client.post(
            f"/api/assessments/assignments/{assignment_id}/review-items/{review_items[0]['question_id']}/complete",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(complete.status_code, 200)
        self.assertTrue(complete.json()["data"]["is_completed"])

        # 重复交卷幂等：不新增结果版本
        repeat = self.client.post(
            f"/api/assessments/assignments/{assignment_id}/submit",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(repeat.status_code, 200)
        db = self.harness.session()
        try:
            from app.modules.assessments.models import AssessmentResult

            count = db.query(AssessmentResult).filter(AssessmentResult.assignment_id == assignment_id).count()
            self.assertEqual(count, 1)
        finally:
            db.close()

        # 人工修分生成新版本；招聘旧公开版本仍对候选人可见，重新公布后才切换
        wrong_question = questions[0]["question_id"]
        fixed = self.client.patch(
            f"/api/assessments/assignments/{assignment_id}/answers/{wrong_question}/score",
            json={"score": 80, "reason": "人工复核：答案等价"},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(fixed.status_code, 200, fixed.text)
        self.assertEqual(fixed.json()["data"]["result"]["version"], 2)
        old_version = self.client.get(
            f"/api/assessments/assignments/{assignment_id}/result",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(old_version.status_code, 200)
        self.assertEqual(old_version.json()["data"]["version"], 1)

        republished = self._post(f"/api/assessments/assignments/{assignment_id}/publish", {})
        self.assertEqual(republished.status_code, 200)
        new_version = self.client.get(
            f"/api/assessments/assignments/{assignment_id}/result",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(new_version.json()["data"]["version"], 2)
        self.assertEqual(new_version.json()["data"]["total_score"], 95.0)

    def test_short_answer_failure_not_scored_zero_and_retry_only_failed(self):
        self._create_question("专业", qtype="short_answer")
        plan = self._post(
            "/api/assessments/plans",
            {
                "purpose": "employee",
                "job_id": 1,
                "job_title": "后端工程师",
                "dimensions": [{"name": "专业", "type": "professional", "weight": 100}],
                "total_question_count": 1,
                "pass_score": 60,
                "mode": "fixed",
            },
        )
        plan_id = plan.json()["data"]["id"]
        self.assertEqual(self._post(f"/api/assessments/plans/{plan_id}/publish", {}).status_code, 200)
        assignment_id = self._assign(plan_id, self.employee)

        detail = self.client.get(
            f"/api/assessments/assignments/{assignment_id}",
            headers=self.harness.headers(self.employee),
        )
        question_id = detail.json()["data"]["questions"][0]["question_id"]
        saved = self.client.put(
            f"/api/assessments/assignments/{assignment_id}/answers/{question_id}",
            json={"answer_text": "我的作答内容"},
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        original = service.default_grade_short_answer
        service.default_grade_short_answer = lambda snapshot, text: (_ for _ in ()).throw(
            RuntimeError("模型超时")
        )
        try:
            submit = self.client.post(
                f"/api/assessments/assignments/{assignment_id}/submit",
                headers=self.harness.headers(self.employee),
            )
            self.assertEqual(submit.status_code, 200, submit.text)
            self.assertEqual(submit.json()["data"]["status"], "grading")
            self.assertEqual(submit.json()["data"]["failed_count"], 1)
            self.assertIsNone(submit.json()["data"]["result"])

            db = self.harness.session()
            try:
                from app.modules.assessments.models import AssessmentAnswer, AssessmentResult

                answer = db.query(AssessmentAnswer).filter(AssessmentAnswer.assignment_id == assignment_id).one()
                self.assertEqual(answer.grading_status, "failed")
                self.assertIsNone(answer.score)
                self.assertEqual(
                    db.query(AssessmentResult).filter(AssessmentResult.assignment_id == assignment_id).count(),
                    0,
                )
            finally:
                db.close()

            no_result = self.client.get(
                f"/api/assessments/assignments/{assignment_id}/result",
                headers=self.harness.headers(self.employee),
            )
            self.assertEqual(no_result.status_code, 404)

            service.default_grade_short_answer = lambda snapshot, text: 80.0
            retry = self.client.post(
                f"/api/assessments/assignments/{assignment_id}/retry-grading",
                headers=self.harness.headers(self.employee),
            )
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertEqual(retry.json()["data"]["status"], "completed")
            self.assertEqual(retry.json()["data"]["result"]["total_score"], 80.0)
            # 员工测评评分完成即可见
            visible = self.client.get(
                f"/api/assessments/assignments/{assignment_id}/result",
                headers=self.harness.headers(self.employee),
            )
            self.assertEqual(visible.status_code, 200)
        finally:
            service.default_grade_short_answer = original


if __name__ == "__main__":
    unittest.main()
