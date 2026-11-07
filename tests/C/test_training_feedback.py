"""C 培训评价：出勤资格、7 天窗口、匿名汇总门槛与主题归纳（契约 §7）。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from c_harness import Harness
from app.modules.training import service


def _iso(days: float) -> str:
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")


class TrainingFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.admin = self.harness.create_user("hr_fb", role="admin")
        self.employee = self.harness.create_user("emp_fb1", role="employee")
        self.others = [self.harness.create_user(f"emp_fb{i}", role="employee") for i in range(2, 7)]
        self.candidate = self.harness.create_user("cand_fb", role="candidate")
        self.client = self.harness.client

    def tearDown(self):
        self.harness.close()

    def _course(self, title="培训课程"):
        response = self.client.post(
            "/api/training/courses",
            json={
                "title": title,
                "material_url": "https://example.com/feedback-course",
                "dimension_names": ["专业"],
            },
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def _session(self, course_id: int, start_days: float = -2, end_days: float = -1):
        response = self.client.post(
            f"/api/training/courses/{course_id}/sessions",
            json={
                "instructor_name": "王老师",
                "starts_at": _iso(start_days),
                "ends_at": _iso(end_days),
            },
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def _attend(self, session_id: int, user):
        response = self.client.post(
            f"/api/training/sessions/{session_id}/attendance",
            json={"employee_user_id": user.id},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _submit(self, course_id: int, user, content=5, instructor=4, comment=None):
        return self.client.post(
            f"/api/training/courses/{course_id}/feedback",
            json={
                "content_score": content,
                "instructor_score": instructor,
                "comment": comment,
            },
            headers=self.harness.headers(user),
        )

    def test_attendance_gate_and_seven_day_window(self):
        course_id = self._course()
        session_id = self._session(course_id)

        missing = self._submit(course_id, self.employee)
        self.assertEqual(missing.status_code, 403)
        self.assertIn("出勤", missing.json()["message"])

        self._attend(session_id, self.employee)
        submitted = self._submit(course_id, self.employee, 5, 4, "内容实用")
        self.assertEqual(submitted.status_code, 200, submitted.text)
        state = submitted.json()["data"]
        self.assertTrue(state["eligible"])
        self.assertEqual(state["feedback"]["status"], "submitted")
        self.assertEqual(state["feedback"]["comment"], "内容实用")

        updated = self.client.patch(
            f"/api/training/courses/{course_id}/feedback",
            json={"content_score": 3, "instructor_score": 2, "comment": "希望增加案例"},
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["data"]["feedback"]["content_score"], 3)

        closed_course = self._course("已关闭课程")
        closed_session = self._session(closed_course, -10, -8)
        self._attend(closed_session, self.employee)
        closed = self._submit(closed_course, self.employee)
        self.assertEqual(closed.status_code, 400)
        self.assertIn("窗口已关闭", closed.json()["message"])

        mine = self.client.get(
            f"/api/training/courses/{closed_course}/feedback/mine",
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(mine.status_code, 200)
        self.assertFalse(mine.json()["data"]["eligible"])

        non_employee = self.client.get(
            f"/api/training/courses/{course_id}/feedback/mine",
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(non_employee.status_code, 403)

    def test_summary_requires_five_and_never_exposes_raw_comments(self):
        course_id = self._course("匿名汇总课程")
        session_id = self._session(course_id)
        users = [self.employee, *self.others]
        for user in users[:4]:
            self._attend(session_id, user)
            self.assertEqual(self._submit(course_id, user, 5, 4, f"私密意见-{user.id}").status_code, 200)

        below = self.client.get(
            f"/api/training/courses/{course_id}/feedback/summary",
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(below.status_code, 200, below.text)
        self.assertFalse(below.json()["data"]["enough_samples"])
        self.assertIsNone(below.json()["data"]["content_avg"])

        self._attend(session_id, users[4])
        self.assertEqual(self._submit(course_id, users[4], 3, 2, "私密意见-最后一份").status_code, 200)

        summary = self.client.get(
            f"/api/training/courses/{course_id}/feedback/summary",
            headers=self.harness.headers(self.admin),
        )
        payload = summary.json()["data"]
        self.assertTrue(payload["enough_samples"])
        self.assertEqual(payload["feedback_count"], 5)
        self.assertEqual(payload["content_avg"], 4.6)
        self.assertEqual(payload["instructor_avg"], 3.6)
        self.assertNotIn("私密意见-", summary.text)
        self.assertEqual(payload["themes"], [])

        forbidden = self.client.get(
            f"/api/training/courses/{course_id}/feedback/summary",
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_cross_session_merge_and_withdraw(self):
        course_id = self._course("跨期课程")
        first = self._session(course_id, -5, -4)
        second = self._session(course_id, -2, -1)
        self._attend(first, self.employee)
        self._attend(second, self.employee)

        self.assertEqual(self._submit(course_id, self.employee, 4, 3, "第一次").status_code, 200)
        self.assertEqual(self._submit(course_id, self.employee, 5, 5).status_code, 200)

        summary = self.client.get(
            f"/api/training/courses/{course_id}/feedback/summary",
            headers=self.harness.headers(self.admin),
        ).json()["data"]
        self.assertEqual(summary["feedback_count"], 1)

        withdrawn = self.client.post(
            f"/api/training/courses/{course_id}/feedback/withdraw",
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertEqual(withdrawn.json()["data"]["feedback"]["status"], "withdrawn")
        self.assertIsNotNone(withdrawn.json()["data"]["feedback"]["withdrawn_at"])

        after_withdraw = self.client.get(
            f"/api/training/courses/{course_id}/feedback/summary",
            headers=self.harness.headers(self.admin),
        ).json()["data"]
        self.assertEqual(after_withdraw["feedback_count"], 0)

        resubmitted = self._submit(course_id, self.employee, 5, 5, "恢复评价")
        self.assertEqual(resubmitted.status_code, 200)
        self.assertEqual(resubmitted.json()["data"]["feedback"]["status"], "submitted")

    def test_theme_threshold_and_model_failure(self):
        course_id = self._course("主题归纳课程")
        session_id = self._session(course_id)
        users = [self.employee, *self.others]
        comments = ["案例太少", "案例太少", "案例太少", "案例太少", "案例太少", "讲师很好"]
        for user, comment in zip(users, comments):
            self._attend(session_id, user)
            self.assertEqual(self._submit(course_id, user, 4, 4, comment).status_code, 200)

        original = service.theme_extractor

        def fake_extractor(items):
            return [["案例偏少"] if "案例" in text else ["讲师互动"] for text in items]

        service.theme_extractor = fake_extractor
        try:
            refresh = self.client.post(
                f"/api/training/courses/{course_id}/feedback-themes/refresh",
                headers=self.harness.headers(self.admin),
            )
            self.assertEqual(refresh.status_code, 200, refresh.text)
            self.assertEqual(refresh.json()["data"]["theme_count"], 1)

            summary = self.client.get(
                f"/api/training/courses/{course_id}/feedback/summary",
                headers=self.harness.headers(self.admin),
            ).json()["data"]
            self.assertEqual(summary["themes"], [{"theme": "案例偏少", "count": 5}])
            self.assertNotIn("案例太少", summary["themes"])
        finally:
            service.theme_extractor = original

        def failing_extractor(items):
            raise RuntimeError("model down")

        service.theme_extractor = failing_extractor
        try:
            failed = self.client.post(
                f"/api/training/courses/{course_id}/feedback-themes/refresh",
                headers=self.harness.headers(self.admin),
            )
            self.assertEqual(failed.status_code, 503)
            self.assertIn("主题归纳失败", failed.json()["message"])
        finally:
            service.theme_extractor = original

        no_comment_course = self._course("无文字意见")
        no_comment_session = self._session(no_comment_course)
        self._attend(no_comment_session, self.employee)
        self.assertEqual(self._submit(no_comment_course, self.employee, 5, 5).status_code, 200)
        empty = self.client.post(
            f"/api/training/courses/{no_comment_course}/feedback-themes/refresh",
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(empty.status_code, 200)
        self.assertFalse(empty.json()["data"]["generated"])
        self.assertEqual(empty.json()["data"]["theme_count"], 0)

    def test_note_and_admin_only_session_permissions(self):
        course_id = self._course("改进说明课程")
        session_id = self._session(course_id)
        self._attend(session_id, self.employee)
        self.assertEqual(self._submit(course_id, self.employee, 4, 3).status_code, 200)

        note = self.client.put(
            f"/api/training/courses/{course_id}/feedback-note",
            json={"note": "下期增加实战案例"},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(note.status_code, 200, note.text)
        self.assertEqual(note.json()["data"]["feedback_note"], "下期增加实战案例")

        mine = self.client.get(
            f"/api/training/courses/{course_id}/feedback/mine",
            headers=self.harness.headers(self.employee),
        ).json()["data"]
        self.assertEqual(mine["improvement_note"], "下期增加实战案例")

        forbidden_note = self.client.put(
            f"/api/training/courses/{course_id}/feedback-note",
            json={"note": "员工不能发布"},
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(forbidden_note.status_code, 403)

        forbidden_session = self.client.get(
            f"/api/training/courses/{course_id}/sessions",
            headers=self.harness.headers(self.employee),
        )
        self.assertEqual(forbidden_session.status_code, 403)

        invalid_range = self.client.post(
            f"/api/training/courses/{course_id}/sessions",
            json={"starts_at": _iso(0), "ends_at": _iso(-1)},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(invalid_range.status_code, 400)

        non_employee_attendance = self.client.post(
            f"/api/training/sessions/{session_id}/attendance",
            json={"employee_user_id": self.candidate.id},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(non_employee_attendance.status_code, 400)


if __name__ == "__main__":
    unittest.main()
