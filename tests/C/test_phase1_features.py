"""C 一期新增功能 API 测试：F10 盘点、培训报名、学习证据、AI 面试间。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
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
        self._seed_approved_config()

    def tearDown(self) -> None:
        self.h.close()

    def _post(self, path, payload, user):
        return self.h.client.post(path, json=payload, headers=Harness.headers(user))

    def _seed_approved_config(self, *, hi_min=4, mid_min=3):
        """新建并批准九宫格标准配置；返回配置 id（未批准则正式评级被拒）。"""
        created = self._post(
            "/api/inventory/grid-configs",
            {
                "performance_bands": {"hi_min": hi_min, "mid_min": mid_min},
                "potential_bands": {"hi_min": hi_min, "mid_min": mid_min},
            },
            self.admin,
        )
        self.assertEqual(created.status_code, 200)
        cid = created.json()["data"]["id"]
        approved = self.h.client.post(
            f"/api/inventory/grid-configs/{cid}/approve", headers=Harness.headers(self.admin))
        self.assertEqual(approved.status_code, 200)
        return cid

    def _get_review(self, rid):
        return self.h.client.get(
            f"/api/inventory/reviews/{rid}", headers=Harness.headers(self.admin)).json()["data"]

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

    def _create_review(self, **scores):
        payload = {"employee_user_id": self.emp.id, "cycle_name": "2026H1"}
        payload.update(scores)
        r = self._post("/api/inventory/reviews", payload, self.admin)
        self.assertEqual(r.status_code, 200)
        return r.json()["data"]["id"]

    def test_confirm_rejects_draft_without_submit(self):
        rid = self._create_review(performance_score=5, potential_manager_score=4, potential_hr_score=4)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 400)
        self.assertIn("提交", r.json()["message"])

    def test_submit_requires_manager_initial_review(self):
        rid = self._create_review(performance_score=5, potential_hr_score=4)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 400)
        self.assertIn("经理初评", r.json()["message"])

    def test_submit_requires_performance_score(self):
        rid = self._create_review(potential_manager_score=4, potential_hr_score=4)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 400)

    def test_confirm_chain_success_and_no_reconfirm(self):
        rid = self._create_review(performance_score=5, potential_manager_score=4, potential_hr_score=3)
        s = self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
        self.assertEqual(s.status_code, 200)
        after_submit = self.h.client.get(
            f"/api/inventory/reviews/{rid}", headers=Harness.headers(self.admin)).json()["data"]
        self.assertEqual(after_submit["status"], "submitted")
        c = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        self.assertEqual(c.status_code, 200)
        data = self.h.client.get(
            f"/api/inventory/reviews/{rid}", headers=Harness.headers(self.admin)).json()["data"]
        self.assertEqual(data["status"], "confirmed")
        self.assertEqual(data["grid_cell"], "hi-mid")
        self.assertEqual(data["reviewer_hr_id"], self.admin.id)
        self.assertIsNotNone(data["confirmed_at"])
        again = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        self.assertEqual(again.status_code, 400)

    def test_confirmed_review_cannot_be_modified(self):
        rid = self._create_review(performance_score=5, potential_manager_score=4, potential_hr_score=4)
        self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
        self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        patch = self.h.client.patch(
            f"/api/inventory/reviews/{rid}", json={"performance_score": 1},
            headers=Harness.headers(self.admin))
        self.assertEqual(patch.status_code, 400)

    def test_confirm_records_grid_config_version(self):
        rid = self._create_review(performance_score=5, potential_manager_score=4, potential_hr_score=3)
        self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
        c = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        self.assertEqual(c.status_code, 200)
        data = self._get_review(rid)
        self.assertEqual(data["grid_cell"], "hi-mid")
        self.assertEqual(data["grid_config_version"], 1)  # 依据当前批准的 v1 标准

    def test_confirm_blocked_without_approved_config(self):
        # 把已批准配置退回草稿 → 无生效标准 → 正式确认被拒
        db = self.h.session()
        try:
            from app.modules.inventory.models import InventoryGridConfig
            from sqlalchemy import select as _select
            cfg = db.scalars(_select(InventoryGridConfig)).one()
            cfg.status = "draft"
            db.commit()
        finally:
            db.close()
        rid = self._create_review(performance_score=5, potential_manager_score=4, potential_hr_score=4)
        self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
        c = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        self.assertEqual(c.status_code, 400)
        self.assertIn("配置", c.json()["message"])
        # 草稿阶段暂不入格（无生效标准）
        self.assertIsNone(self._get_review(rid)["grid_cell"])
        # 重新批准后确认可产出正式评级
        cfg_id = self._seed_approved_config()
        c2 = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(self.admin))
        self.assertEqual(c2.status_code, 200)
        self.assertEqual(self._get_review(rid)["grid_cell"], "hi-hi")

    def test_missing_performance_stays_pending(self):
        # 缺绩效：草稿/提交都不入格，确认被拒（缺绩效不得入格）
        rid = self._create_review(potential_manager_score=4, potential_hr_score=4)
        self.assertIsNone(self._get_review(rid)["grid_cell"])
        s = self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
        self.assertEqual(s.status_code, 400)

    def test_grid_config_requires_valid_bands(self):
        bad = self._post(
            "/api/inventory/grid-configs",
            {"performance_bands": {"hi_min": 3, "mid_min": 4}, "potential_bands": {"hi_min": 4, "mid_min": 3}},
            self.admin,
        )
        self.assertEqual(bad.status_code, 400)

    def test_dispute_idp_and_summary(self):
        rid = self._post(
            "/api/inventory/reviews",
            {"employee_user_id": self.emp.id, "cycle_name": "2026H1",
             "performance_score": 5, "potential_manager_score": 4, "potential_hr_score": 5},
            self.admin,
        ).json()["data"]["id"]
        self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
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
             "performance_score": 5, "potential_manager_score": 4, "potential_hr_score": 5},
            self.admin,
        ).json()["data"]["id"]
        self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(self.admin))
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

    def test_capacity_waitlist_and_promotion(self):
        course = self.h.client.post(
            "/api/training/courses",
            json={"title": "限名额", "description": "x", "material_url": "http://x", "capacity": 1},
            headers=Harness.headers(self.admin)).json()["data"]
        self.assertEqual(course["capacity"], 1)
        emp2 = self.h.create_user("e2", role="employee")
        first = self.h.client.post(
            "/api/training/enrollments", json={"course_id": course["id"]},
            headers=Harness.headers(self.emp)).json()["data"]
        self.assertEqual(first["status"], "pending")
        second = self.h.client.post(
            "/api/training/enrollments", json={"course_id": course["id"]},
            headers=Harness.headers(emp2)).json()["data"]
        self.assertEqual(second["status"], "waitlisted")
        self.h.client.post(
            f"/api/training/enrollments/{first['id']}/decide", json={"status": "approved"},
            headers=Harness.headers(self.admin))
        # 通过后取消即释放名额，最早候补进入 awaiting_confirmation（非正式占位，待本人确认）
        cancelled = self.h.client.post(
            f"/api/training/enrollments/{first['id']}/decide", json={"status": "cancelled"},
            headers=Harness.headers(self.admin))
        self.assertEqual(cancelled.json()["data"]["status"], "cancelled")
        rows = self.h.client.get("/api/training/enrollments", headers=Harness.headers(self.admin)).json()["data"]
        statuses = {row["id"]: row["status"] for row in rows}
        self.assertEqual(statuses[second["id"]], "awaiting_confirmation")
        # 待确认阶段管理员不能直接审批，须员工先确认
        prem = self.h.client.post(
            f"/api/training/enrollments/{second['id']}/decide", json={"status": "approved"},
            headers=Harness.headers(self.admin))
        self.assertEqual(prem.status_code, 400)
        # 他人不能确认本人名额
        other = self.h.client.post(
            f"/api/training/enrollments/{second['id']}/confirm", headers=Harness.headers(self.emp))
        self.assertEqual(other.status_code, 403)
        # 本人确认 → pending，随后仍走审批规则
        confirmed = self.h.client.post(
            f"/api/training/enrollments/{second['id']}/confirm", headers=Harness.headers(emp2))
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["data"]["status"], "pending")
        self.h.client.post(
            f"/api/training/enrollments/{second['id']}/decide", json={"status": "approved"},
            headers=Harness.headers(self.admin))
        third_emp = self.h.create_user("e3", role="employee")
        third = self.h.client.post(
            "/api/training/enrollments", json={"course_id": course["id"]},
            headers=Harness.headers(third_emp)).json()["data"]
        self.assertEqual(third["status"], "waitlisted")
        blocked = self.h.client.post(
            f"/api/training/enrollments/{third['id']}/decide", json={"status": "approved"},
            headers=Harness.headers(self.admin))
        self.assertEqual(blocked.status_code, 400)

    def test_waitlist_offer_confirmation_timeout(self):
        """候补名额通知后 24h 未确认：释放并通知下一位（超时通知下位）。"""
        from datetime import datetime, timedelta

        from app.modules.training.models import TrainingEnrollment

        course = self.h.client.post(
            "/api/training/courses",
            json={"title": "限1", "description": "x", "material_url": "http://x", "capacity": 1},
            headers=Harness.headers(self.admin)).json()["data"]
        emp2 = self.h.create_user("w2", role="employee")
        emp3 = self.h.create_user("w3", role="employee")
        first = self.h.client.post(
            "/api/training/enrollments", json={"course_id": course["id"]},
            headers=Harness.headers(self.emp)).json()["data"]
        self.h.client.post(
            f"/api/training/enrollments/{first['id']}/decide", json={"status": "approved"},
            headers=Harness.headers(self.admin))
        self.h.client.post(
            "/api/training/enrollments", json={"course_id": course["id"]},
            headers=Harness.headers(emp2))
        self.h.client.post(
            "/api/training/enrollments", json={"course_id": course["id"]},
            headers=Harness.headers(emp3))
        # 释放名额 → emp2 递补为待确认
        self.h.client.post(
            f"/api/training/enrollments/{first['id']}/decide", json={"status": "cancelled"},
            headers=Harness.headers(self.admin))
        # 人为把 emp2 的确认期限调到过去
        rows = self.h.client.get("/api/training/enrollments", headers=Harness.headers(self.admin)).json()["data"]
        awaiting = next(r for r in rows if r["status"] == "awaiting_confirmation")
        db = self.h.session()
        try:
            row = db.get(TrainingEnrollment, awaiting["id"])
            row.expires_at = datetime.now() - timedelta(hours=1)
            db.commit()
        finally:
            db.close()
        # 定时任务释放超时待确认名额并递补下一位
        released = self.h.client.post(
            "/api/training/enrollments/release-expired", headers=Harness.headers(self.admin))
        self.assertEqual(released.json()["data"]["released"], 1)
        after = self.h.client.get("/api/training/enrollments", headers=Harness.headers(self.admin)).json()["data"]
        st = {r["id"]: r["status"] for r in after}
        self.assertEqual(st[awaiting["id"]], "expired")
        self.assertIn("awaiting_confirmation", st.values())
        # 已失效名额不能再确认
        late = self.h.client.post(
            f"/api/training/enrollments/{awaiting['id']}/confirm", headers=Harness.headers(emp2))
        self.assertEqual(late.status_code, 400)

    def test_release_expired_and_requires_admin(self):
        from datetime import datetime, timedelta

        from app.modules.training.models import TrainingEnrollment
        eid = self.h.client.post(
            "/api/training/enrollments", json={"course_id": self.course},
            headers=Harness.headers(self.emp)).json()["data"]["id"]
        db = self.h.session()
        try:
            row = db.get(TrainingEnrollment, eid)
            row.expires_at = datetime.now() - timedelta(hours=1)
            db.commit()
        finally:
            db.close()
        denied = self.h.client.post(
            "/api/training/enrollments/release-expired", headers=Harness.headers(self.emp))
        self.assertEqual(denied.status_code, 403)
        released = self.h.client.post(
            "/api/training/enrollments/release-expired", headers=Harness.headers(self.admin))
        self.assertEqual(released.json()["data"]["released"], 1)
        rows = self.h.client.get("/api/training/enrollments", headers=Harness.headers(self.admin)).json()["data"]
        self.assertEqual({row["id"]: row["status"] for row in rows}[eid], "expired")


class PrejobPlanApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("pa", role="admin")
        self.emp = self.h.create_user("pe", role="employee")
        self.mentor = self.h.create_user("pm", role="manager")
        self.other = self.h.create_user("po", role="employee")
        self.course = self.h.client.post(
            "/api/training/courses",
            json={"title": "岗前安全", "description": "x", "material_url": "http://x"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def _payload(self):
        return {
            "employee_user_id": self.emp.id,
            "target_job_title": "工程师",
            "goal": "90 天胜任",
            "phases": [
                {"name": "融入", "goal": "文化与制度", "mentor_user_id": self.mentor.id,
                 "courses": [{"course_id": self.course}]},
                {"name": "上岗", "goal": "岗位技能", "mentor_user_id": self.mentor.id,
                 "courses": [{"course_id": self.course, "note": "复训"}]},
            ],
        }

    def _create(self):
        r = self.h.client.post(
            "/api/training/prejob-plans", json=self._payload(), headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def test_create_and_view(self):
        plan = self._create()
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(plan["version"], 1)
        self.assertEqual(len(plan["phases"]), 2)
        self.assertEqual(plan["phases"][0]["mentor_user_id"], self.mentor.id)
        self.assertEqual(plan["phases"][0]["courses"][0]["course"]["course_id"], self.course)

    def test_assign_then_revise_versions(self):
        plan = self._create()
        a = self.h.client.post(
            f"/api/training/prejob-plans/{plan['id']}/assign", headers=Harness.headers(self.admin))
        self.assertEqual(a.json()["data"]["status"], "assigned")
        rev = self.h.client.post(
            f"/api/training/prejob-plans/{plan['id']}/revise", headers=Harness.headers(self.admin))
        self.assertEqual(rev.json()["data"]["version"], 2)
        self.assertEqual(rev.json()["data"]["status"], "draft")
        self.assertEqual(len(rev.json()["data"]["phases"]), 2)

    def test_rejects_nonexistent_and_inactive_course(self):
        bad = dict(self._payload())
        bad["phases"][0]["courses"] = [{"course_id": 999999}]
        r = self.h.client.post(
            "/api/training/prejob-plans", json=bad, headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 400)
        self.h.client.patch(
            f"/api/training/courses/{self.course}", json={"status": "inactive"},
            headers=Harness.headers(self.admin))
        r2 = self.h.client.post(
            "/api/training/prejob-plans", json=self._payload(), headers=Harness.headers(self.admin))
        self.assertEqual(r2.status_code, 400)

    def test_employee_only_sees_own(self):
        plan = self._create()
        denied = self.h.client.get(
            f"/api/training/prejob-plans/{plan['id']}", headers=Harness.headers(self.other))
        self.assertEqual(denied.status_code, 403)
        mine = self.h.client.get(
            f"/api/training/prejob-plans/{plan['id']}", headers=Harness.headers(self.emp))
        self.assertEqual(mine.status_code, 200)
        lst = self.h.client.get(
            "/api/training/prejob-plans", headers=Harness.headers(self.other)).json()["data"]
        self.assertEqual(lst, [])

    def test_employee_cannot_assign(self):
        plan = self._create()
        r = self.h.client.post(
            f"/api/training/prejob-plans/{plan['id']}/assign", headers=Harness.headers(self.emp))
        self.assertEqual(r.status_code, 403)


class LearningEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("a", role="admin")
        self.emp = self.h.create_user("e", role="employee")
        self.course_id = self.h.client.post(
            "/api/training/courses", json={"title": "C", "description": "x", "material_url": "http://x"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        plan = self.h.client.post(
            "/api/training/plans",
            json={"employee_user_id": self.emp.id,
                  "items": [{"course_id": self.course_id, "dimension_name": "专业"}]},
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

    def _confirm(self):
        return self.h.client.post(
            f"/api/training/learning-records/{self.record_id}/confirm-completion",
            headers=Harness.headers(self.admin))

    def _issue_token(self, *, minutes_ago: float) -> str:
        from app.models import User
        from app.modules.training import service

        db = self.h.session()
        try:
            token = service.issue_study_session(
                db, self.record_id, user=db.get(User, self.emp.id),
                now=datetime.now() - timedelta(minutes=minutes_ago),
            )["session_token"]
        finally:
            db.close()
        return token

    def _submit_study(self, token: str):
        from app.models import User
        from app.modules.training import service
        from app.modules.training.schemas import EvidenceSubmitRequest

        db = self.h.session()
        try:
            return service.submit_evidence(
                db,
                EvidenceSubmitRequest(
                    learning_record_id=self.record_id,
                    evidence_type="study_session",
                    payload={"session_token": token},
                ),
                user=db.get(User, self.emp.id),
            )
        finally:
            db.close()

    def test_client_cannot_self_report_score_or_verifier(self):
        # 客户端直传 score/verified_by 或自定义证据类型一律拒绝（分数/验证人只能由服务端产生）
        r = self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "video_heartbeat",
                  "payload": {"duration": 600}, "score": 100, "verified_by": "hr"},
            headers=Harness.headers(self.emp))
        self.assertEqual(r.status_code, 422)
        r2 = self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "attendance", "score": 100},
            headers=Harness.headers(self.emp))
        self.assertEqual(r2.status_code, 422)

    def test_attendance_evidence_requires_real_attendance_and_completes(self):
        self.assertEqual(self._confirm().status_code, 400)
        # 没有出勤记录时不能造出证据
        r = self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "attendance"},
            headers=Harness.headers(self.emp))
        self.assertEqual(r.status_code, 400)
        session = self.h.client.post(
            f"/api/training/courses/{self.course_id}/sessions",
            json={"instructor_name": "讲师", "starts_at": "2026-11-09T09:00:00",
                  "ends_at": "2026-11-09T10:00:00"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        self.h.client.post(
            f"/api/training/sessions/{session}/attendance",
            json={"employee_user_id": self.emp.id},
            headers=Harness.headers(self.admin))
        r = self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "attendance"},
            headers=Harness.headers(self.emp)).json()["data"]
        self.assertEqual(r["score"], 100.0)
        self.assertEqual(r["verified_by"], "system.attendance")
        # 同一出勤记录不能重复计入
        again = self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "attendance"},
            headers=Harness.headers(self.emp))
        self.assertEqual(again.status_code, 400)
        ok_confirm = self._confirm()
        self.assertEqual(ok_confirm.status_code, 200)
        self.assertTrue(ok_confirm.json()["data"]["formal_completed"])

    def test_study_session_rejects_short_forged_and_replay(self):
        from app.core.errors import BizError

        # 不足最短有效时长（5 分钟）不计入证据
        with self.assertRaises(BizError):
            self._submit_study(self._issue_token(minutes_ago=1))
        token = self._issue_token(minutes_ago=6)
        first = self._submit_study(token)
        self.assertLess(first.score, 60)  # 6/45 分钟，未达完成阈值
        # 同一会话凭证重放被拒
        with self.assertRaises(BizError):
            self._submit_study(token)
        # 篡改签发时间但签名不符被拒
        forged = token[:-1] + ("0" if token[-1] != "0" else "1")
        with self.assertRaises(BizError):
            self._submit_study(forged)
        # 证据分未达阈值时不能认定正式完成
        self.assertEqual(self._confirm().status_code, 400)

    def test_evidence_only_employee_of_record(self):
        other = self.h.create_user("e2", role="employee")
        r = self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "attendance"},
            headers=Harness.headers(other))
        self.assertEqual(r.status_code, 403)
        r2 = self.h.client.post(
            "/api/training/learning-records/"
            f"{self.record_id}/study-sessions",
            headers=Harness.headers(other))
        self.assertEqual(r2.status_code, 403)
        r3 = self.h.client.post(
            f"/api/training/learning-records/{self.record_id}/study-sessions",
            headers=Harness.headers(self.emp))
        self.assertEqual(r3.status_code, 200)

    def test_formal_completion_appends_growth_event_once(self):
        from sqlalchemy import select
        from app.modules.training.models import TrainingGrowthEvent

        session = self.h.client.post(
            f"/api/training/courses/{self.course_id}/sessions",
            json={"instructor_name": "讲师", "starts_at": "2026-11-09T09:00:00",
                  "ends_at": "2026-11-09T10:00:00"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        self.h.client.post(
            f"/api/training/sessions/{session}/attendance",
            json={"employee_user_id": self.emp.id}, headers=Harness.headers(self.admin))
        self.h.client.post(
            "/api/training/learning-evidence",
            json={"learning_record_id": self.record_id, "evidence_type": "attendance"},
            headers=Harness.headers(self.emp))
        self.assertEqual(self._confirm().status_code, 200)

        def _formal_events():
            db = self.h.session()
            try:
                return list(db.scalars(select(TrainingGrowthEvent).where(
                    TrainingGrowthEvent.employee_user_id == self.emp.id,
                    TrainingGrowthEvent.event_type == "course_formally_completed")))
            finally:
                db.close()

        events = _formal_events()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.ref_type, "training_plan_item")
        self.assertFalse(ev.payload_json["self_reported"])
        self.assertEqual(ev.payload_json["evidence_score"], 100.0)
        self.assertIn("system.attendance", ev.payload_json["evidence_providers"])
        self.assertEqual(ev.payload_json["learning_record_id"], self.record_id)
        # 重复确认不重复追加成长事件
        self.assertEqual(self._confirm().status_code, 200)
        self.assertEqual(len(_formal_events()), 1)



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
