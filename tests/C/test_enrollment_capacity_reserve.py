"""C06 候补预留容量口径 + C08 心跳三键分离回归（REQ-MASTER-20261112-004136-C 修复轮）。

C06 修复口径：
- 报名满员判定 = 正式占位（pending/pending_admin_review/approved）+ 待确认预留；
- 候补递补至 awaiting_confirmation 后，新报名只能进候补，不得以 pending 抢走待确认名额；
- 本人确认候补时排除自身预留（确认只是预留→占位，总量不变）；
- 超时释放后名额可再次递补。

C08 修复口径：
- 发送周期 / 空闲窗口 / 计费容差三键分离（config.study_heartbeat_*）；
- 相邻心跳间隔 ≤（空闲窗口 + 容差）计入学时；超出视为离开不计；
- 会话签发响应同时下发 heartbeat_interval_seconds 与 idle_seconds。

运行：backend/.venv/Scripts/python.exe -m pytest tests/C/test_enrollment_capacity_reserve.py -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

from c_harness import Harness

from app.config import settings
from app.modules.training.models import TrainingEnrollment


class EnrollmentCapacityReserveTest(unittest.TestCase):
    """C06：预留名额不被新报名抢占，本人确认不受后来占位误伤。"""

    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("r_admin", role="admin")
        self.emp_a = self.h.create_user("r_a", role="employee")
        self.emp_b = self.h.create_user("r_b", role="employee")
        self.emp_c = self.h.create_user("r_c", role="employee")

    def tearDown(self) -> None:
        self.h.close()

    def _course(self, **cfg):
        body = {"title": "预留口径", "description": "x", "material_url": "http://x"}
        body.update(cfg)
        r = self.h.client.post("/api/training/courses", json=body, headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]["id"]

    def _apply(self, course_id, user):
        r = self.h.client.post("/api/training/enrollments", json={"course_id": course_id},
                               headers=Harness.headers(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def _confirm(self, enrollment_id, user):
        return self.h.client.post(f"/api/training/enrollments/{enrollment_id}/confirm",
                                  headers=Harness.headers(user))

    def _cancel(self, enrollment_id, user):
        # 取消走 decide 动作（与既有 decide-guards 用例一致）
        return self.h.client.post(f"/api/training/enrollments/{enrollment_id}/decide",
                                  json={"status": "cancelled"}, headers=Harness.headers(user))

    def _status(self, eid):
        db = self.h.session()
        try:
            return db.get(TrainingEnrollment, eid).status
        finally:
            db.close()

    def test_reserved_seat_not_stolen_by_new_enrollment(self):
        # 容量 1：A 占位 → B 候补 → A 取消 → B 递补待确认 → C 报名必须候补而非 pending
        cid = self._course(capacity=1)  # admin 审批模式：A 报名即 pending 占位
        a = self._apply(cid, self.emp_a)
        self.assertEqual(a["status"], "pending")
        b = self._apply(cid, self.emp_b)
        self.assertEqual(b["status"], "waitlisted")
        self.assertEqual(self._cancel(a["id"], self.admin).status_code, 200, "管理员取消占位释放名额")
        self.assertEqual(self._status(b["id"]), "awaiting_confirmation", "A 取消后 B 递补待确认")
        c = self._apply(cid, self.emp_c)
        self.assertEqual(
            c["status"], "waitlisted",
            "C06 修复点：待确认预留计入容量，新报名不得以 pending 抢走 B 的名额",
        )
        # B 本人确认成功（修复前：C 的 pending 占位使 confirm 400）
        r = self._confirm(b["id"], self.emp_b)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(b["id"]), "pending", "确认后转审批占位，仍计入占用")
        self.assertEqual(self._status(c["id"]), "waitlisted", "C 保持候补，不重复占位")

    def test_confirm_capacity_check_excludes_own_reservation(self):
        # 容量 2：A/B 占位 → C 候补 → A 取消 → C 递补待确认 → D 报名应候补（1 占位+1 预留=满）
        # C 本人确认排除自身预留：1 占位 < 2，确认成功
        cid = self._course(capacity=2)
        a = self._apply(cid, self.emp_a)
        self._apply(cid, self.emp_b)
        c = self._apply(cid, self.emp_c)
        self.assertEqual(c["status"], "waitlisted")
        self.assertEqual(self._cancel(a["id"], self.admin).status_code, 200)
        self.assertEqual(self._status(c["id"]), "awaiting_confirmation")
        emp_d = self.h.create_user("r_d", role="employee")
        d = self._apply(cid, emp_d)
        self.assertEqual(d["status"], "waitlisted", "正式占用+预留已达容量，D 只能候补")
        r = self._confirm(c["id"], self.emp_c)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(c["id"]), "pending")
        self.assertEqual(self._status(d["id"]), "waitlisted")

    def test_expired_reservation_releases_and_repromotes(self):
        # 容量 1：B 递补后不确认超时释放 → 名额可再次递补最早候补
        cid = self._course(capacity=1)
        a = self._apply(cid, self.emp_a)
        b = self._apply(cid, self.emp_b)
        self._cancel(a["id"], self.admin)
        self.assertEqual(self._status(b["id"]), "awaiting_confirmation")
        c = self._apply(cid, self.emp_c)
        self.assertEqual(c["status"], "waitlisted", "预留期间 C 只能候补")
        # 直接把 B 的确认期限改成已过期，走释放扫描
        db = self.h.session()
        try:
            row = db.get(TrainingEnrollment, b["id"])
            row.expires_at = datetime.now() - timedelta(hours=25)
            db.commit()
        finally:
            db.close()
        from app.modules.training import service
        db = self.h.session()
        try:
            out = service.release_expired_enrollments(db)
        finally:
            db.close()
        self.assertEqual(out["released"], 1, out)
        self.assertEqual(self._status(b["id"]), "expired", "超时候补被释放")
        self.assertEqual(self._status(c["id"]), "awaiting_confirmation", "释放后名额递补给 C")


class HeartbeatGapToleranceTest(unittest.TestCase):
    """C08：间隔 ≤（空闲窗口+容差）计费；超出不计；三键分离下发。"""

    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("t_admin", role="admin")
        self.emp = self.h.create_user("t_emp", role="employee")
        self.course_id = self.h.client.post(
            "/api/training/courses", json={"title": "T", "description": "x", "material_url": "http://x"},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
        plan = self.h.client.post(
            "/api/training/plans",
            json={"employee_user_id": self.emp.id,
                  "items": [{"course_id": self.course_id, "dimension_name": "专业"}]},
            headers=Harness.headers(self.admin)).json()["data"]["id"]
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

    def _issue_at(self, base):
        from app.models import User
        from app.modules.training import service

        db = self.h.session()
        try:
            return service.issue_study_session(
                db, self.record_id, user=db.get(User, self.emp.id), now=base)
        finally:
            db.close()

    def _heartbeat(self, token, at):
        from app.models import User
        from app.modules.training import service
        from app.modules.training.schemas import StudyHeartbeatRequest

        db = self.h.session()
        try:
            return service.record_study_heartbeat(
                db, self.record_id, StudyHeartbeatRequest(session_token=token),
                user=db.get(User, self.emp.id), now=at)
        finally:
            db.close()

    def test_session_response_separates_interval_and_idle(self):
        base = datetime.now()
        out = self._issue_at(base)
        self.assertEqual(
            out["heartbeat_interval_seconds"], settings.study_heartbeat_interval_seconds,
            "发送周期使用独立配置键，不再取空闲窗口",
        )
        self.assertEqual(out["idle_seconds"], settings.study_heartbeat_idle_seconds)
        self.assertNotEqual(
            settings.study_heartbeat_interval_seconds, settings.study_heartbeat_idle_seconds,
            "三键分离后发送周期应明显小于空闲窗口（默认 30 vs 90）",
        )

    def test_gap_within_tolerance_still_counted(self):
        # 间隔略超空闲窗口但在容差内（90+10=100）：模拟发送周期贴合窗口的调度抖动
        base = datetime.now()
        token = self._issue_at(base)["session_token"]
        self._heartbeat(token, base)
        self._heartbeat(token, base + timedelta(seconds=60))
        r = self._heartbeat(token, base + timedelta(seconds=95))  # 95 > 90 但 ≤ 100
        self.assertGreater(
            r["active_minutes"], 1.0, "容差内的轻微抖动不得整段丢弃",
        )
        self.assertEqual(r["active_minutes"], round((60 + 35) / 60, 2), "计费按实际间隔 35s（容差段封顶前）")

    def test_gap_beyond_tolerance_not_counted(self):
        base = datetime.now()
        token = self._issue_at(base)["session_token"]
        self._heartbeat(token, base)
        self._heartbeat(token, base + timedelta(seconds=60))
        r = self._heartbeat(token, base + timedelta(seconds=200))  # 远超 90+10
        self.assertEqual(r["active_minutes"], 1.0, "离开静默段仍不计学时")

    def test_tolerance_configurable(self):
        base = datetime.now()
        token = self._issue_at(base)["session_token"]
        with mock.patch.object(settings, "study_heartbeat_idle_seconds", 30), \
                mock.patch.object(settings, "study_heartbeat_gap_tolerance_seconds", 0):
            self._heartbeat(token, base)
            self._heartbeat(token, base + timedelta(seconds=20))   # ≤30 计费
            r = self._heartbeat(token, base + timedelta(seconds=60))  # 40 > 30+0 不计
            self.assertAlmostEqual(r["active_minutes"], round(20 / 60, 2), places=2,
                                   msg="容差置 0 时退回严格窗口口径")
