"""C06 报名审批状态×动作白名单反例（REQ-MASTER-20261111-233731-C）。

验收口径：释放名额后管理员直批 waitlisted 仍被拒绝；递补后未确认不能审批；
经理课未经理批准不能最终批准；免审批课程本人确认后按现行规则完成；
取消/24h 超时/重复确认与批准/名额不变量逐项覆盖。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from c_harness import Harness

from app.modules.training.models import TrainingEnrollment


class EnrollmentDecideGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("g_admin", role="admin")
        self.emp = self.h.create_user("g_emp", role="employee")
        self.mgr = self.h.create_user("g_mgr", role="dept_manager")
        self.h.set_department(self.emp, 10)
        self.h.set_department(self.mgr, 10)

    def tearDown(self) -> None:
        self.h.close()

    def _course(self, **cfg):
        body = {"title": "白名单", "description": "x", "material_url": "http://x"}
        body.update(cfg)
        r = self.h.client.post("/api/training/courses", json=body, headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]["id"]

    def _apply(self, course_id, user):
        r = self.h.client.post("/api/training/enrollments", json={"course_id": course_id},
                               headers=Harness.headers(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def _decide(self, eid, status, user=None):
        return self.h.client.post(f"/api/training/enrollments/{eid}/decide",
                                  json={"status": status}, headers=Harness.headers(user or self.admin))

    def _status(self, eid):
        db = self.h.session()
        try:
            return db.get(TrainingEnrollment, eid).status
        finally:
            db.close()

    def _occupying(self, course_id):
        rows = self.h.client.get(f"/api/training/enrollments?course_id={course_id}",
                                 headers=Harness.headers(self.admin)).json()["data"]
        return sum(1 for r in rows if r["status"] in ("pending", "pending_admin_review", "approved"))

    def test_admin_cannot_approve_waitlisted_even_with_free_seat(self):
        # 容量 2：两占位 + 两候补；取消一个占位 → 最早候补递补待确认，
        # 第二个候补仍 waitlisted 且此时有空位——管理员直批仍须拒绝
        cid = self._course(capacity=2)
        emp2 = self.h.create_user("g_emp2", role="employee")
        emp3 = self.h.create_user("g_emp3", role="employee")
        emp4 = self.h.create_user("g_emp4", role="employee")
        first = self._apply(cid, self.emp)
        self._apply(cid, emp2)
        third = self._apply(cid, emp3)
        fourth = self._apply(cid, emp4)
        self.assertEqual(third["status"], "waitlisted")
        self.assertEqual(fourth["status"], "waitlisted")
        self.assertEqual(self._decide(first["id"], "cancelled").status_code, 200)
        self.assertEqual(self._status(third["id"]), "awaiting_confirmation")
        self.assertEqual(self._status(fourth["id"]), "waitlisted")
        self.assertLess(self._occupying(cid), 2)  # 确有物理空位
        r = self._decide(fourth["id"], "approved")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self._status(fourth["id"]), "waitlisted")
        # 拒绝同样不在候补白名单内
        r = self._decide(fourth["id"], "rejected")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self._status(fourth["id"]), "waitlisted")

    def test_waitlisted_cancel_is_legitimate_exit_and_promotes_next(self):
        cid = self._course(capacity=1)
        emp2 = self.h.create_user("g_emp2", role="employee")
        emp3 = self.h.create_user("g_emp3", role="employee")
        first = self._apply(cid, self.emp)
        second = self._apply(cid, emp2)
        third = self._apply(cid, emp3)
        self.assertEqual(self._decide(first["id"], "approved").status_code, 200)
        # 候补排队可合法取消退出，不授予名额
        r = self._decide(second["id"], "cancelled")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(second["id"]), "cancelled")
        # 取消候补不释放占位（未占位），最早候补仍按序递补：此处名额仍满，third 保持候补
        self.assertEqual(self._status(third["id"]), "waitlisted")
        # 取消已批准占位 → third 递补待确认
        self.assertEqual(self._decide(first["id"], "cancelled").status_code, 200)
        self.assertEqual(self._status(third["id"]), "awaiting_confirmation")

    def test_promoted_offer_not_approvable_before_self_confirm(self):
        cid = self._course(capacity=1)
        emp2 = self.h.create_user("g_emp2", role="employee")
        first = self._apply(cid, self.emp)
        second = self._apply(cid, emp2)
        self._decide(first["id"], "cancelled")
        self.assertEqual(self._status(second["id"]), "awaiting_confirmation")
        r = self._decide(second["id"], "approved")
        self.assertEqual(r.status_code, 400)
        self.assertIn("确认", r.json()["message"])
        # 本人确认后进入 pending，管理员按审批规则通过
        r = self.h.client.post(f"/api/training/enrollments/{second['id']}/confirm",
                               headers=Harness.headers(emp2))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._decide(second["id"], "approved").json()["data"]["status"], "approved")

    def test_manager_course_final_approval_requires_manager_then_review(self):
        cid = self._course(approval_mode="manager", has_cost=True, capacity=1)
        emp2 = self.h.create_user("g_emp2", role="employee")
        self.h.set_department(emp2, 10)
        first = self._apply(cid, self.emp)
        second = self._apply(cid, emp2)
        # 管理员旧入口不能批经理课
        self.assertEqual(self._decide(first["id"], "approved").status_code, 400)
        # 未经经理审批，复核入口不可最终批准
        r = self.h.client.post(f"/api/training/enrollments/{first['id']}/admin-review",
                               json={"status": "approved"}, headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 400)
        # 经理拒绝释放名额 → second 待确认；管理员仍不能越过本人确认
        r = self.h.client.post(f"/api/training/enrollments/{first['id']}/manager-decide",
                               json={"status": "rejected"}, headers=Harness.headers(self.mgr))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(second["id"]), "awaiting_confirmation")
        self.assertEqual(self._decide(second["id"], "approved").status_code, 400)
        # 本人确认 → 经理通过 → 涉费转复核 → 管理员复核才最终批准
        self.h.client.post(f"/api/training/enrollments/{second['id']}/confirm",
                           headers=Harness.headers(emp2))
        r = self.h.client.post(f"/api/training/enrollments/{second['id']}/manager-decide",
                               json={"status": "approved"}, headers=Harness.headers(self.mgr))
        self.assertEqual(r.json()["data"]["status"], "pending_admin_review")
        r = self.h.client.post(f"/api/training/enrollments/{second['id']}/admin-review",
                               json={"status": "approved"}, headers=Harness.headers(self.admin))
        self.assertEqual(r.json()["data"]["status"], "approved")

    def test_none_mode_waitlist_requires_self_confirm(self):
        cid = self._course(approval_mode="none", capacity=1)
        emp2 = self.h.create_user("g_emp2", role="employee")
        first = self._apply(cid, self.emp)
        second = self._apply(cid, emp2)
        self.assertEqual(first["status"], "approved")
        self._decide(first["id"], "cancelled")
        # 免审批课程也不能跳过本人确认直接批准候补
        self.assertEqual(self._decide(second["id"], "approved").status_code, 400)
        self.h.client.post(f"/api/training/enrollments/{second['id']}/confirm",
                           headers=Harness.headers(emp2))
        self.assertEqual(self._status(second["id"]), "approved")

    def test_repeat_decide_and_repeat_confirm_rejected(self):
        cid = self._course(capacity=1)
        emp2 = self.h.create_user("g_emp2", role="employee")
        first = self._apply(cid, self.emp)
        second = self._apply(cid, emp2)
        self.assertEqual(self._decide(first["id"], "approved").json()["data"]["status"], "approved")
        self.assertEqual(self._decide(first["id"], "approved").status_code, 400)  # 重复批准
        self._decide(first["id"], "cancelled")
        self.assertEqual(self._decide(first["id"], "cancelled").status_code, 400)  # 重复取消
        r = self.h.client.post(f"/api/training/enrollments/{second['id']}/confirm",
                               headers=Harness.headers(emp2))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.h.client.post(f"/api/training/enrollments/{second['id']}/confirm",
                               headers=Harness.headers(emp2))
        self.assertEqual(r.status_code, 400)  # 重复确认

    def test_expired_pending_cannot_be_approved(self):
        cid = self._course()
        row = self._apply(cid, self.emp)
        db = self.h.session()
        try:
            db.get(TrainingEnrollment, row["id"]).expires_at = datetime.now() - timedelta(hours=1)
            db.commit()
        finally:
            db.close()
        r = self._decide(row["id"], "approved")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._status(row["id"]), "expired")

    def test_single_seat_invariant_across_full_waitlist_chain(self):
        # 容量 1 全程：批准→取消→递补→确认→批准，任何时刻占位不超过 1
        cid = self._course(capacity=1)
        emp2 = self.h.create_user("g_emp2", role="employee")
        emp3 = self.h.create_user("g_emp3", role="employee")
        first = self._apply(cid, self.emp)
        second = self._apply(cid, emp2)
        third = self._apply(cid, emp3)
        self.assertLessEqual(self._occupying(cid), 1)
        self._decide(first["id"], "approved")
        self.assertLessEqual(self._occupying(cid), 1)
        self._decide(first["id"], "cancelled")
        self.assertEqual(self._status(second["id"]), "awaiting_confirmation")
        self.assertEqual(self._status(third["id"]), "waitlisted")
        self.h.client.post(f"/api/training/enrollments/{second['id']}/confirm",
                           headers=Harness.headers(emp2))
        self.assertLessEqual(self._occupying(cid), 1)
        # 第三个候补即使面对审批入口也不得占位
        self.assertEqual(self._decide(third["id"], "approved").status_code, 400)
        self._decide(second["id"], "approved")
        self.assertLessEqual(self._occupying(cid), 1)
        self.assertEqual(self._status(third["id"]), "waitlisted")


if __name__ == "__main__":
    unittest.main()