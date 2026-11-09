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

    def test_summary_export_and_audit(self):
        rid = self._post(
            "/api/inventory/reviews",
            {"employee_user_id": self.emp.id, "cycle_name": "2026H1",
             "performance_score": 5, "potential_hr_score": 5},
            self.admin,
        ).json()["data"]["id"]
        self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        r = self.h.client.get(
            "/api/inventory/summary/export?cycle_name=2026H1", headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("total,,1", r.text)
        self.assertNotIn(str(self.emp.id), r.text)  # 不含个人身份
        # 员工不可导出
        r2 = self.h.client.get("/api/inventory/summary/export", headers=Harness.headers(self.emp))
        self.assertEqual(r2.status_code, 403)
        # 导出写了审计事件
        from sqlalchemy import select
        from app.modules.inventory.models import InventoryEvent
        db = self.h.session()
        try:
            events = list(db.scalars(select(InventoryEvent).where(
                InventoryEvent.event_type == "summary_exported")))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].payload_json["cycle_name"], "2026H1")
        finally:
            db.close()


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


class TrainingNotificationsTest(unittest.TestCase):
    """F09：课程指派/报名审批/延期/到期/逾期 的 D 域通知落地。"""

    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("a", role="admin")
        self.emp = self.h.create_user("e", role="employee")
        self.course = self.h.client.post(
            "/api/training/courses",
            json={"title": "必修安全课", "description": "x", "material_url": "http://x"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def _plan(self, due_date: str | None) -> int:
        item = {"course_id": self.course, "dimension_name": "专业"}
        if due_date:
            item["due_date"] = due_date
        plan = self.h.client.post(
            "/api/training/plans",
            json={"employee_user_id": self.emp.id, "items": [item]},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        self.h.client.post(f"/api/training/plans/{plan}/assign", headers=Harness.headers(self.admin))
        return plan

    def _item_id(self, plan_id: int) -> int:
        detail = self.h.client.get(
            f"/api/training/plans/{plan_id}", headers=Harness.headers(self.admin)).json()["data"]
        return detail["items"][0]["id"]

    def _count(self, event_type: str) -> int:
        from sqlalchemy import select
        from app.modules.notifications.models import Notification
        db = self.h.session()
        try:
            return len(list(db.scalars(select(Notification).where(Notification.event_type == event_type))))
        finally:
            db.close()

    def test_course_assigned_notification(self):
        self._plan("2026-11-20")
        self.assertEqual(self._count("course.assigned"), 1)

    def test_enrollment_reviewed_notification(self):
        eid = self.h.client.post(
            "/api/training/enrollments", json={"course_id": self.course, "priority": "required"},
            headers=Harness.headers(self.emp)).json()["data"]["id"]
        self.h.client.post(
            f"/api/training/enrollments/{eid}/decide", json={"status": "approved"},
            headers=Harness.headers(self.admin))
        self.assertEqual(self._count("course.enrollment_reviewed"), 1)

    def test_extension_requires_reason_and_notifies(self):
        plan = self._plan("2026-11-20")
        item = self._item_id(plan)
        bad = self.h.client.post(
            f"/api/training/plan-items/{item}/extension", json={"new_due_date": "2026-12-01"},
            headers=Harness.headers(self.admin))
        self.assertEqual(bad.status_code, 422)
        ok = self.h.client.post(
            f"/api/training/plan-items/{item}/extension",
            json={"new_due_date": "2026-12-01", "reason": "项目冲突"},
            headers=Harness.headers(self.admin))
        self.assertEqual(ok.json()["data"]["due_date"], "2026-12-01")
        self.assertEqual(self._count("training.extension_decided"), 1)

    def test_scan_due_and_overdue(self):
        plan = self._plan("2026-11-11")
        item = self._item_id(plan)
        scan = self.h.client.post(
            "/api/training/reminders/scan", json={"today": "2026-11-09", "days_ahead": 3},
            headers=Harness.headers(self.admin)).json()["data"]
        self.assertEqual(scan["due"]["emitted"], 1)
        self.assertEqual(scan["overdue"]["emitted"], 0)
        self.assertEqual(self._count("training.due_reminder"), 1)
        # 延期到过去 → 逾期扫描命中，且重复扫描不新增行（幂等）
        self.h.client.post(
            f"/api/training/plan-items/{item}/extension",
            json={"new_due_date": "2026-11-05", "reason": "补录"},
            headers=Harness.headers(self.admin))
        self.h.client.post(
            "/api/training/reminders/scan", json={"today": "2026-11-09"},
            headers=Harness.headers(self.admin))
        self.h.client.post(
            "/api/training/reminders/scan", json={"today": "2026-11-09"},
            headers=Harness.headers(self.admin))
        self.assertEqual(self._count("training.overdue"), 1)

    def test_scan_requires_admin(self):
        r = self.h.client.post(
            "/api/training/reminders/scan", json={"today": "2026-11-09"},
            headers=Harness.headers(self.emp))
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
