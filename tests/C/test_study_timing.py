"""C08 有效学习区间结算：服务端时序反例（审核建议 20261111 第 4 项）。

验收对照：
- 乱序/重复/过期心跳不增加学时；客户端不能自报“已学分钟”或 is_active；
- 空闲窗口与单次累计上限配置化（settings.study_heartbeat_*），默认沿用已确认基线；
- 多端交替心跳同一区间只计一次；结算后会话作废，心跳/重放均拒绝；
- 合法学习中断后可恢复并累计结算；正式完成仍按课程条件（见 test_phase1_features）。

运行：backend/.venv/Scripts/python.exe -m pytest tests/C/test_study_timing.py -v
真实浏览器前后台/资源状态验证与 MySQL 行锁并发为未测项（见 MASTER 更新记录）。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

from c_harness import Harness

from app.config import settings
from app.core.errors import BizError


class StudyTimingTest(unittest.TestCase):
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
                db, self.record_id, user=db.get(User, self.emp.id), now=base)["session_token"]
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

    def _submit_study(self, token):
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

    # ---- 顺序 / 重复 / 过期 ----

    def test_out_of_order_heartbeat_rejected_without_state_change(self):
        base = datetime.now()
        token = self._issue_at(base)
        self._heartbeat(token, base)
        self._heartbeat(token, base + timedelta(seconds=60))
        with self.assertRaises(BizError) as ctx:
            self._heartbeat(token, base + timedelta(seconds=30))
        self.assertIn("乱序", str(ctx.exception))
        # 被拒心跳不改状态：后续合法心跳仍从 +60 基准累计
        r = self._heartbeat(token, base + timedelta(seconds=120))
        self.assertEqual(r["heartbeat_count"], 3)
        self.assertAlmostEqual(r["active_minutes"], 2.0, places=2)

    def test_duplicate_same_second_heartbeat_idempotent(self):
        base = datetime.now()
        token = self._issue_at(base)
        self._heartbeat(token, base)
        self._heartbeat(token, base + timedelta(seconds=60))
        dup = self._heartbeat(token, base + timedelta(seconds=60))
        self.assertEqual(dup["heartbeat_count"], 2, "重复心跳不累计次数")
        self.assertAlmostEqual(dup["active_minutes"], 1.0, places=2)
        r = self._heartbeat(token, base + timedelta(seconds=120))
        self.assertEqual(r["heartbeat_count"], 3)
        self.assertAlmostEqual(r["active_minutes"], 2.0, places=2)

    def test_expired_session_heartbeat_rejected(self):
        base = datetime.now()
        token = self._issue_at(base)
        expired_at = base + timedelta(minutes=settings.study_session_ttl_minutes + 1)
        with self.assertRaises(BizError) as ctx:
            self._heartbeat(token, expired_at)
        self.assertIn("过期", str(ctx.exception))

    def test_client_cannot_self_report_minutes_or_active_flag(self):
        base = datetime.now()
        token = self._issue_at(base)
        for extra in ({"active_minutes": 999}, {"is_active": True}, {"minutes": 60}):
            r = self.h.client.post(
                f"/api/training/learning-records/{self.record_id}/study-heartbeat",
                json={"session_token": token, **extra},
                headers=Harness.headers(self.emp))
            self.assertEqual(r.status_code, 422, f"额外字段必须被拒绝：{extra}")

    # ---- 空闲窗口 / 单次上限（配置化）----

    def test_idle_gap_beyond_window_not_counted(self):
        base = datetime.now()
        token = self._issue_at(base)
        self._heartbeat(token, base)
        self._heartbeat(token, base + timedelta(seconds=60))
        r = self._heartbeat(token, base + timedelta(seconds=660))  # 离开 10 分钟
        self.assertAlmostEqual(r["active_minutes"], 1.0, places=2)

    def test_idle_window_configurable(self):
        base = datetime.now()
        token = self._issue_at(base)
        with mock.patch.object(settings, "study_heartbeat_idle_seconds", 30):
            self._heartbeat(token, base)
            self._heartbeat(token, base + timedelta(seconds=20))   # +20s
            r = self._heartbeat(token, base + timedelta(seconds=60))  # 间隔 40s > 30s 窗口
            self.assertAlmostEqual(r["active_minutes"], round(20 / 60, 2), places=2)
            r = self._heartbeat(token, base + timedelta(seconds=80))  # +20s
            self.assertAlmostEqual(r["active_minutes"], round(40 / 60, 2), places=2)

    def test_single_interval_credit_cap_configurable(self):
        base = datetime.now()
        token = self._issue_at(base)
        with mock.patch.object(settings, "study_heartbeat_idle_seconds", 120), \
                mock.patch.object(settings, "study_heartbeat_credit_cap_seconds", 30):
            self._heartbeat(token, base)
            r = self._heartbeat(token, base + timedelta(seconds=60))  # 间隔合法但封顶 30s
            self.assertAlmostEqual(r["active_minutes"], 0.5, places=2)
            r = self._heartbeat(token, base + timedelta(seconds=120))
            self.assertAlmostEqual(r["active_minutes"], 1.0, places=2)

    # ---- 多端并发 / 结算唯一 ----

    def test_multi_device_interleaved_heartbeats_count_once(self):
        base = datetime.now()
        token = self._issue_at(base)
        # 两台设备共用凭证交替心跳：同一区间只能累计一次，总学时=真实经过时间
        for i in range(7):
            r = self._heartbeat(token, base + timedelta(seconds=30 * i))
        self.assertEqual(r["heartbeat_count"], 7)
        self.assertAlmostEqual(r["active_minutes"], 3.0, places=2)

    def test_heartbeat_after_settlement_rejected(self):
        base = datetime.now()
        token = self._issue_at(base)
        for i in range(7):
            self._heartbeat(token, base + timedelta(seconds=60 * i))
        self._submit_study(token)
        with self.assertRaises(BizError) as ctx:
            self._heartbeat(token, base + timedelta(seconds=420))
        self.assertIn("已结算", str(ctx.exception))

    def test_min_heartbeat_count_configurable(self):
        base = datetime.now()
        token = self._issue_at(base)
        with mock.patch.object(settings, "study_session_min_minutes", 1.0):
            self._heartbeat(token, base)
            self._heartbeat(token, base + timedelta(seconds=60))  # 1 分钟但仅 2 次心跳
            with self.assertRaises(BizError) as ctx:
                self._submit_study(token)
            self.assertIn("学习交互不足", str(ctx.exception))
            self._heartbeat(token, base + timedelta(seconds=120))
            ev = self._submit_study(token)
            self.assertEqual(ev.verified_by, "system.study_session")

    # ---- 合法学习可恢复 ----

    def test_legitimate_learning_resumes_after_pause_and_settles(self):
        base = datetime.now()
        token = self._issue_at(base)
        for i in range(3):  # 前段 2 分钟
            self._heartbeat(token, base + timedelta(seconds=60 * i))
        # 中断 8 分钟（静默段不计），恢复后继续学习 4 分钟
        for i in range(5):
            self._heartbeat(token, base + timedelta(seconds=600 + 60 * i))
        ev = self._submit_study(token)
        detail = ev.payload_json
        self.assertAlmostEqual(detail["minutes"], 6.0, places=2)
        self.assertEqual(ev.verified_by, "system.study_session")


if __name__ == "__main__":
    unittest.main()