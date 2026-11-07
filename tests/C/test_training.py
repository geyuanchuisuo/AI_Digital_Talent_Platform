"""C 培训成长：真实课程、AI 草稿边界、分配/修订、员工自报与成长记录（契约 §5）。"""

from __future__ import annotations

import unittest

from c_harness import Harness
from app.modules.training import drafting, service


class TrainingFlowTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.admin = self.harness.create_user("hr2", role="admin")
        self.employee = self.harness.create_user("emp2", role="employee")
        self.other_employee = self.harness.create_user("emp3", role="employee")
        self.candidate = self.harness.create_user("cand2", role="candidate")
        self.client = self.harness.client
        self.course_id = self._create_course()

    def tearDown(self):
        self.harness.close()

    def _create_course(self, url="https://example.com/course/1"):
        response = self.client.post(
            "/api/training/courses",
            json={
                "title": "后端基础训练",
                "description": "索引、事务与并发",
                "material_url": url,
                "dimension_names": ["专业"],
            },
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def _manual_plan(self, employee_id=None):
        response = self.client.post(
            "/api/training/plans",
            json={
                "employee_user_id": employee_id or self.employee.id,
                "target_job_title": "后端工程师",
                "items": [
                    {
                        "course_id": self.course_id,
                        "dimension_name": "专业",
                        "reason": "补齐索引基础",
                    }
                ],
            },
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def test_course_url_must_be_http_and_employee_only(self):
        invalid = self.client.post(
            "/api/training/courses",
            json={"title": "伪造课程", "material_url": "javascript:alert(1)"},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(invalid.status_code, 400)

        non_employee = self.client.post(
            "/api/training/plans",
            json={
                "employee_user_id": self.candidate.id,
                "items": [{"course_id": self.course_id}],
            },
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(non_employee.status_code, 400)
        self.assertIn("员工", non_employee.json()["message"])

    def test_ai_draft_assign_progress_and_revise(self):
        original = service.drafting.generate_draft

        def fake_generator(**kwargs):
            self.assertIn(self.course_id, [course["id"] for course in kwargs["courses"]])
            return drafting.DraftResult(
                items=[
                    drafting.DraftItem(
                        course_id=self.course_id,
                        dimension_name="专业",
                        reason="补齐数据库基础",
                    )
                ],
                gap_message="系统设计课程暂缺",
            )

        service.drafting.generate_draft = fake_generator
        try:
            draft = self.client.post(
                "/api/training/plans/draft",
                json={
                    "employee_user_id": self.employee.id,
                    "target_job_title": "后端工程师",
                    "dimension_gaps": ["专业"],
                },
                headers=self.harness.headers(self.admin),
            )
            self.assertEqual(draft.status_code, 200, draft.text)
            payload = draft.json()["data"]
            self.assertTrue(payload["generated"])
            self.assertEqual(payload["plan"]["status"], "draft")
            self.assertEqual(payload["plan"]["source"], "ai")
            self.assertEqual(payload["gap_message"], "系统设计课程暂缺")
            plan_id = payload["plan"]["id"]

            before_assign = self.client.get(
                "/api/training/my/plans", headers=self.harness.headers(self.employee)
            ).json()["data"]["items"]
            self.assertEqual(before_assign, [])

            not_employee = self.client.get(
                "/api/training/my/plans", headers=self.harness.headers(self.candidate)
            )
            self.assertEqual(not_employee.status_code, 403)

            assigned = self.client.post(
                f"/api/training/plans/{plan_id}/assign",
                headers=self.harness.headers(self.admin),
            )
            self.assertEqual(assigned.status_code, 200, assigned.text)
            self.assertEqual(assigned.json()["data"]["status"], "assigned")

            my_plans = self.client.get(
                "/api/training/my/plans", headers=self.harness.headers(self.employee)
            ).json()["data"]["items"]
            self.assertEqual(len(my_plans), 1)
            item = my_plans[0]["items"][0]
            self.assertEqual(item["learning"]["status"], "not_started")
            item_id = item["id"]

            forbidden = self.client.put(
                f"/api/training/plan-items/{item_id}/progress",
                json={"status": "learning"},
                headers=self.harness.headers(self.other_employee),
            )
            self.assertEqual(forbidden.status_code, 403)

            progress = self.client.put(
                f"/api/training/plan-items/{item_id}/progress",
                json={"status": "completed", "note": "完成视频学习"},
                headers=self.harness.headers(self.employee),
            )
            self.assertEqual(progress.status_code, 200, progress.text)
            self.assertTrue(progress.json()["data"]["is_self_reported"])

            growth = self.client.get(
                "/api/training/growth", headers=self.harness.headers(self.employee)
            ).json()["data"]["items"]
            event_types = [item["event_type"] for item in growth]
            self.assertIn("training_assigned", event_types)
            self.assertIn("course_completed", event_types)

            # 重复标记完成不重复写成长事件
            self.client.put(
                f"/api/training/plan-items/{item_id}/progress",
                json={"status": "completed"},
                headers=self.harness.headers(self.employee),
            )
            growth_again = self.client.get(
                "/api/training/growth", headers=self.harness.headers(self.employee)
            ).json()["data"]["items"]
            completed_events = [
                item for item in growth_again if item["event_type"] == "course_completed"
            ]
            self.assertEqual(len(completed_events), 1)

            detail = self.client.get(
                f"/api/training/plans/{plan_id}", headers=self.harness.headers(self.admin)
            )
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(len(detail.json()["data"]["versions"]), 1)
            self.assertEqual(detail.json()["data"]["versions"][0]["status"], "assigned")

            revised = self.client.post(
                f"/api/training/plans/{plan_id}/revise",
                headers=self.harness.headers(self.admin),
            )
            self.assertEqual(revised.status_code, 200, revised.text)
            self.assertEqual(revised.json()["data"]["version"], 2)
            self.assertEqual(revised.json()["data"]["status"], "draft")

            versions = self.client.get(
                f"/api/training/plans/{plan_id}", headers=self.harness.headers(self.admin)
            ).json()["data"]["versions"]
            self.assertEqual(len(versions), 2)
            statuses = {item["version"]: item["status"] for item in versions}
            self.assertEqual(statuses[1], "assigned")
            self.assertEqual(statuses[2], "draft")
        finally:
            service.drafting.generate_draft = original

    def test_draft_reports_missing_courses_and_model_failure(self):
        deactivated = self.client.patch(
            f"/api/training/courses/{self.course_id}",
            json={"status": "inactive"},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(deactivated.status_code, 200)
        no_course = self.client.post(
            "/api/training/plans/draft",
            json={"employee_user_id": self.employee.id, "target_job_title": "岗位"},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(no_course.status_code, 200)
        self.assertFalse(no_course.json()["data"]["generated"])

        # 目录存在但模型失败：明确 503，不静默生成空计划
        self.client.patch(
            f"/api/training/courses/{self.course_id}",
            json={"status": "active"},
            headers=self.harness.headers(self.admin),
        )
        original = service.drafting.generate_draft
        service.drafting.generate_draft = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("模型不可用")
        )
        try:
            failed = self.client.post(
                "/api/training/plans/draft",
                json={"employee_user_id": self.employee.id, "target_job_title": "岗位"},
                headers=self.harness.headers(self.admin),
            )
            self.assertEqual(failed.status_code, 503)
        finally:
            service.drafting.generate_draft = original

    def test_recruitment_skips_training_draft_and_employee_needs_result(self):
        from app.modules.assessments.models import AssessmentAssignment, AssessmentResult

        db = self.harness.session()
        try:
            recruitment = AssessmentAssignment(
                plan_id=1,
                plan_version=1,
                paper_id=1,
                purpose="recruitment",
                user_id=self.candidate.id,
                job_title="后端工程师",
                status="completed",
                idempotency_key="rec-skip",
            )
            employee_pending = AssessmentAssignment(
                plan_id=2,
                plan_version=1,
                paper_id=2,
                purpose="employee",
                user_id=self.employee.id,
                job_title="后端工程师",
                status="in_progress",
                idempotency_key="emp-pending",
            )
            db.add_all([recruitment, employee_pending])
            db.commit()
            recruitment_id = recruitment.id
            employee_id = employee_pending.id

            result = AssessmentResult(
                assignment_id=employee_id,
                version=1,
                dimension_scores_json=[
                    {"name": "专业", "type": "professional", "weight": 100, "score": 55}
                ],
                total_score=55,
                pass_score=60,
                passed=0,
                grading_snapshot_json={"prompt_version": "stub"},
            )
            db.add(result)
            db.commit()
        finally:
            db.close()

        skipped = self.client.post(
            f"/api/training/plans/draft-from-assessment/{recruitment_id}",
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(skipped.status_code, 200)
        self.assertFalse(skipped.json()["data"]["generated"])
        self.assertIn("招聘", skipped.json()["data"]["reason"])

        # 员工有结果：进入生成流程（此处用替身避免真实模型）
        original = service.drafting.generate_draft
        service.drafting.generate_draft = lambda **kwargs: drafting.DraftResult(
            items=[drafting.DraftItem(course_id=self.course_id, reason="补齐短板")],
            gap_message="",
        )
        try:
            generated = self.client.post(
                f"/api/training/plans/draft-from-assessment/{employee_id}",
                headers=self.harness.headers(self.admin),
            )
            self.assertEqual(generated.status_code, 200, generated.text)
            self.assertTrue(generated.json()["data"]["generated"])
            self.assertEqual(generated.json()["data"]["plan"]["employee_user_id"], self.employee.id)
        finally:
            service.drafting.generate_draft = original


if __name__ == "__main__":
    unittest.main()
