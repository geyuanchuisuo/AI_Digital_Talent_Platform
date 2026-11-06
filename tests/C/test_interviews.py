"""C 人工面试：手动轮次、角色裁剪、完成后反馈、不计算总分（契约 §4）。"""

from __future__ import annotations

import unittest

from c_harness import Harness


class InterviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.admin = self.harness.create_user("hr1", role="hr")
        self.candidate = self.harness.create_user("cand1", role="candidate")
        self.interviewer = self.harness.create_user("tech1", role="employee")
        self.outsider = self.harness.create_user("other1", role="employee")
        self.client = self.harness.client

    def tearDown(self):
        self.harness.close()

    def _create_round(self):
        response = self.client.post(
            "/api/interviews/rounds",
            json={
                "candidate_user_id": self.candidate.id,
                "job_id": 7,
                "job_title": "后端工程师",
                "round_no": 1,
                "round_name": "技术一面",
                "scheduled_at": "2026-11-08T10:00:00",
                "mode": "online",
                "location_or_link": "https://meet.example.com/abc",
                "interviewer_user_ids": [self.interviewer.id],
                "internal_note": "重点关注并发经验",
            },
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def test_rounds_visibility_and_feedback_rules(self):
        round_id = self._create_round()

        my_rounds = self.client.get(
            "/api/interviews/my/rounds", headers=self.harness.headers(self.candidate)
        ).json()["data"]["items"]
        self.assertEqual(len(my_rounds), 1)
        self.assertEqual(my_rounds[0]["view"], "candidate")
        self.assertNotIn("internal_note", my_rounds[0])

        candidate_detail = self.client.get(
            f"/api/interviews/rounds/{round_id}", headers=self.harness.headers(self.candidate)
        )
        self.assertEqual(candidate_detail.status_code, 200)
        self.assertNotIn("internal_note", candidate_detail.json()["data"])
        self.assertNotIn("feedback", candidate_detail.json()["data"])

        forbidden = self.client.get(
            f"/api/interviews/rounds/{round_id}", headers=self.harness.headers(self.outsider)
        )
        self.assertEqual(forbidden.status_code, 403)

        interviewer_detail = self.client.get(
            f"/api/interviews/rounds/{round_id}", headers=self.harness.headers(self.interviewer)
        )
        self.assertEqual(interviewer_detail.status_code, 200)
        self.assertEqual(interviewer_detail.json()["data"]["internal_note"], "重点关注并发经验")
        self.assertEqual(len(interviewer_detail.json()["data"]["events"]), 1)

        # 未完成不能写反馈
        early = self.client.put(
            f"/api/interviews/rounds/{round_id}/feedback",
            json={"evaluation": "提前评价", "recommendation": "advance"},
            headers=self.harness.headers(self.interviewer),
        )
        self.assertEqual(early.status_code, 400)

        completed = self.client.patch(
            f"/api/interviews/rounds/{round_id}",
            json={"status": "completed"},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["data"]["status"], "completed")

        saved = self.client.put(
            f"/api/interviews/rounds/{round_id}/feedback",
            json={
                "evaluation": "并发与数据库基础扎实",
                "evidence": "能解释索引与事务隔离",
                "strengths": "表达清晰",
                "weaknesses": "系统设计经验偏少",
                "recommendation": "advance",
            },
            headers=self.harness.headers(self.interviewer),
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        feedback = saved.json()["data"]
        self.assertEqual(feedback["recommendation"], "advance")
        self.assertEqual(feedback["version"], 1)
        self.assertNotIn("total_score", feedback)

        candidate_feedback = self.client.get(
            f"/api/interviews/rounds/{round_id}/feedback",
            headers=self.harness.headers(self.candidate),
        )
        self.assertEqual(candidate_feedback.status_code, 403)
        admin_feedback = self.client.get(
            f"/api/interviews/rounds/{round_id}/feedback", headers=self.harness.headers(self.admin)
        )
        self.assertEqual(admin_feedback.status_code, 200)

        rescheduled = self.client.patch(
            f"/api/interviews/rounds/{round_id}",
            json={"scheduled_at": "2026-11-09T14:00:00", "mode": "onsite"},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(rescheduled.status_code, 200)
        detail = self.client.get(
            f"/api/interviews/rounds/{round_id}", headers=self.harness.headers(self.admin)
        ).json()["data"]
        event_types = [event["event_type"] for event in detail["events"]]
        self.assertIn("created", event_types)
        self.assertIn("completed", event_types)
        self.assertIn("rescheduled", event_types)

        cancelled = self.client.post(
            f"/api/interviews/rounds/{round_id}/cancel",
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["data"]["status"], "cancelled")

    def test_round_requires_candidate_or_profile(self):
        response = self.client.post(
            "/api/interviews/rounds",
            json={"round_name": "无关联轮次", "interviewer_user_ids": [self.interviewer.id]},
            headers=self.harness.headers(self.admin),
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
