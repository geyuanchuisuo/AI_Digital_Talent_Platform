"""C 一期新增功能 API 测试：F10 盘点、培训报名、学习证据、AI 面试间。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from c_harness import Harness


class _JudgeStub:
    def __init__(self, score=85.0) -> None:
        self.score = score

    def __call__(self, *args, **kwargs):
        return {"score": self.score, "feedback": "ok"}


class InventoryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("a", role="admin")
        self.emp = self.h.create_user("e", role="employee")

    def tearDown(self) -> None:
        self.h.close()

    def _post(self, path, payload, user):
        return self.h.client.post(path, json=payload, headers=Harness.headers(user))

    def test_review_lifecycle_and_visibility(self):
        r = self._post(
            "/api/inventory/reviews",
            {"employee_user_id": self.emp.id, "cycle_name": "2026H1",
             "performance_score": 5, "potential_manager_score": 4, "potential_hr_score": 4},
            self.admin,
        )
        self.assertEqual(r.status_code, 200)
        rid = r.json()["data"]["id"]
        r2 = self.h.client.get(f"/api/inventory/reviews/{rid}", headers=Harness.headers(self.admin))
        self.assertEqual(r2.json()["data"]["grid_cell"], "hi-hi")
        # 员工本人看不到内部评级
        r3 = self.h.client.get(f"/api/inventory/reviews/{rid}", headers=Harness.headers(self.emp))
        data = r3.json()["data"]
        self.assertNotIn("grid_cell", data)

    def test_dispute_idp_and_summary(self):
        rid = self._post(
            "/api/inventory/reviews",
            {"employee_user_id": self.emp.id, "cycle_name": "2026H1",
             "performance_score": 5, "potential_hr_score": 5},
            self.admin,
        ).json()["data"]["id"]
        self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        # 异议
        d = self.h.client.post(
            f"/api/inventory/reviews/{rid}/disputes", json={"content": "不同意"},
            headers=Harness.headers(self.emp))
        self.assertEqual(d.status_code, 200)
        # IDP
        idp = self.h.client.put(
            f"/api/inventory/reviews/{rid}/idp",
            json={"content_json": {"goal": "晋升"}, "status": "confirmed"},
            headers=Harness.headers(self.emp))
        self.assertEqual(idp.status_code, 200)
        # 汇总（5 条同格才可见）
        s = self.h.client.get("/api/inventory/summary", headers=Harness.headers(self.admin))
        self.assertEqual(s.status_code, 200)
        self.assertEqual(s.json()["data"]["total"], 1)

    def test_talent_pool(self):
        p = self._post("/api/inventory/pools", {"employee_user_id": self.emp.id, "pool_type": "high_potential"}, self.admin)
        self.assertEqual(p.status_code, 200)
        pid = p.json()["data"]["id"]
        d = self.h.client.post(f"/api/inventory/pools/{pid}/decide", json={"status": "approved"}, headers=Harness.headers(self.admin))
        self.assertEqual(d.json()["data"]["status"], "approved")


class TrainingEnrollmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("a", role="admin")
        self.emp = self.h.create_user("e", role="employee")
        self.course = self.h.client.post(
            "/api/training/courses", json={"title": "C 安全", "description": "x", "material_url": "http://x"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def test_apply_decide(self):
        r = self.h.client.post(
            "/api/training/enrollments", json={"course_id": self.course, "priority": "elective"},
            headers=Harness.headers(self.emp))
        self.assertEqual(r.status_code, 200)
        eid = r.json()["data"]["id"]
        self.assertEqual(r.json()["data"]["status"], "pending")
        d = self.h.client.post(
            f"/api/training/enrollments/{eid}/decide", json={"status": "approved"},
            headers=Harness.headers(self.admin))
        self.assertEqual(d.json()["data"]["status"], "approved")


class LearningEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("a", role="admin")
        self.emp = self.h.create_user("e", role="employee")
        course = self.h.client.post(
            "/api/training/courses", json={"title": "C", "description": "x", "material_url": "http://x"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        plan = self.h.client.post(
            "/api/training/plans",
            json={"employee_user_id": self.emp.id, "items": [{"course_id": course, "dimension_name": "专业"}]},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        # 分配计划后员工才能上报学习进度，从而创建学习记录
        self.h.client.post(f"/api/training/plans/{plan}/assign", headers=Harness.headers(self.admin))
        detail = self.h.client.get(f"/api/training/plans/{plan}", headers=Harness.headers(self.admin)).json()["data"]
        item_id = detail["items"][0]["id"]
        self.h.client.put(
            f"/api/training/plan-items/{item_id}/progress",
            json={"status": "learning", "note": "开始学习"},
            headers=Harness.headers(self.emp))
        detail = self.h.client.get(f"/api/training/plans/{plan}", headers=Harness.headers(self.admin)).json()["data"]
        self.record_id = detail["items"][0]["learning"]["record_id"]

    def tearDown(self) -> None:
        self.h.close()

    def test_evidence_blocks_completion_without_evidence(self):
        r = self.h.client.post(
            f"/api/training/learning-records/{self.record_id}/confirm-completion",
            headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 400)
        e = self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "video_heartbeat",
                  "payload": {"duration": 600}, "score": 90},
            headers=Harness.headers(self.emp))
        self.assertEqual(e.status_code, 200)
        r2 = self.h.client.post(
            f"/api/training/learning-records/{self.record_id}/confirm-completion",
            headers=Harness.headers(self.admin))
        self.assertTrue(r2.json()["data"]["formal_completed"])


class AiInterviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("a", role="admin")
        self.cand = self.h.create_user("c", role="employee")

    def tearDown(self) -> None:
        self.h.close()

    def test_session_question_answer_finish(self):
        import app.modules.interviews.ai_router as ai_router_mod
        ai_router_mod.default_judge_answer = _JudgeStub(88.0)
        s = self.h.client.post(
            "/api/ai-interviews/sessions", json={"candidate_user_id": self.cand.id},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        self.h.client.post(f"/api/ai-interviews/sessions/{s}/start", headers=Harness.headers(self.cand))
        q = self.h.client.post(
            f"/api/ai-interviews/sessions/{s}/questions",
            json={"question_text": "你是谁"}, headers=Harness.headers(self.admin)).json()["data"]["id"]
        ans = self.h.client.post(
            f"/api/ai-interviews/questions/{q}/answer", json={"answer_text": "我是候选人"},
            headers=Harness.headers(self.cand))
        self.assertEqual(ans.json()["data"]["status"], "graded")
        self.assertEqual(ans.json()["data"]["ai_score"], 88.0)
        fin = self.h.client.post(f"/api/ai-interviews/sessions/{s}/finish", headers=Harness.headers(self.cand))
        self.assertEqual(fin.json()["data"]["overall_score"], 88.0)
        # 列表：管理员可见全部，候选人仅本人，无关员工为空
        mine = self.h.client.get("/api/ai-interviews/sessions", headers=Harness.headers(self.cand)).json()["data"]
        self.assertEqual([row["id"] for row in mine], [s])
        self.assertEqual(mine[0]["status"], "completed")
        other = self.h.create_user("o", role="employee")
        self.assertEqual(
            self.h.client.get("/api/ai-interviews/sessions", headers=Harness.headers(other)).json()["data"], [])
        admin_rows = self.h.client.get("/api/ai-interviews/sessions", headers=Harness.headers(self.admin)).json()["data"]
        self.assertIn(s, [row["id"] for row in admin_rows])


if __name__ == "__main__":
    unittest.main()
