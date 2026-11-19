"""C09 盘点管理写入的对象级负责范围反例（REQ-MASTER-20261111-233731-C）。

验收口径：至少两个部门、范围 HR 与全量授权分别覆盖更新/提交/确认及其余
按 ID 写入/导出入口；外部门请求拒绝且记录、状态、版本与事件数量不变。
范围 HR 构造：hr 角色 data_scope 改为 dept + 本人 department_id（A06 契约）。
"""

from __future__ import annotations

import unittest

from sqlalchemy import select

from c_harness import Harness

from app.models import SysRole
from app.modules.inventory.models import InventoryEvent, InventoryReview


class InventoryScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.admin = self.h.create_user("scope_admin", role="admin")
        self.emp = self.h.create_user("scope_emp10", role="employee")
        self.other = self.h.create_user("scope_emp20", role="employee")
        self.h.set_department(self.emp, 10)
        self.h.set_department(self.other, 20)
        self.mgr10 = self.h.create_user("scope_mgr10", role="dept_manager")
        self.mgr20 = self.h.create_user("scope_mgr20", role="dept_manager")
        self.h.set_department(self.mgr10, 10)
        self.h.set_department(self.mgr20, 20)
        self._seed_approved_config()

    def tearDown(self) -> None:
        self.h.close()

    # ---- 构造辅助

    def _hr(self, username: str, *, scope: str, department_id: int | None = None):
        """hr 账号；scope=dept 时把 hr 角色 data_scope 改为 dept（每测试独立库）。"""
        user = self.h.create_user(username, role="hr")
        if department_id is not None:
            self.h.set_department(user, department_id)
        if scope != "all":
            db = self.h.session()
            try:
                role = db.execute(select(SysRole).where(SysRole.code == "hr")).scalar_one()
                role.data_scope = scope
                db.commit()
            finally:
                db.close()
        return user

    def _seed_approved_config(self):
        r = self.h.client.post(
            "/api/inventory/grid-configs",
            json={"performance_bands": {"hi_min": 4, "mid_min": 3},
                  "potential_bands": {"hi_min": 4, "mid_min": 3}},
            headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        cid = r.json()["data"]["id"]
        r = self.h.client.post(f"/api/inventory/grid-configs/{cid}/approve",
                               headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200, r.text)

    def _review(self, employee, *, submit=False, confirm=False):
        """admin 发起完整复核；经理初评由对应部门经理本人提交。"""
        r = self.h.client.post(
            "/api/inventory/reviews",
            json={"employee_user_id": employee.id, "cycle_name": "2026H1",
                  "performance_score": 5, "potential_hr_score": 4},
            headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["data"]["id"]
        mgr = self.mgr10 if employee.department_id == 10 else self.mgr20
        r = self.h.client.post(
            f"/api/inventory/reviews/{rid}/manager-assessment",
            json={"potential_manager_score": 4}, headers=Harness.headers(mgr))
        self.assertEqual(r.status_code, 200, r.text)
        if submit or confirm:
            r = self.h.client.post(f"/api/inventory/reviews/{rid}/submit",
                                   headers=Harness.headers(self.admin))
            self.assertEqual(r.status_code, 200, r.text)
        if confirm:
            r = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm",
                                   headers=Harness.headers(self.admin))
            self.assertEqual(r.status_code, 200, r.text)
        return rid

    def _event_count(self):
        db = self.h.session()
        try:
            return len(db.execute(select(InventoryEvent)).scalars().all())
        finally:
            db.close()

    def _review_row(self, rid):
        db = self.h.session()
        try:
            return db.get(InventoryReview, rid)
        finally:
            db.close()

    def _assert_rejected_unchanged(self, response, rid, *, status):
        self.assertEqual(response.status_code, 403, response.text)
        row = self._review_row(rid)
        self.assertEqual(row.status, status)
        return row

    # ---- 更新/提交/确认 × 外部门拒绝且无任何副作用

    def test_scoped_hr_update_out_of_department_rejected(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        rid = self._review(self.other)
        before = self._event_count()
        r = self.h.client.patch(f"/api/inventory/reviews/{rid}",
                                json={"summary": "越权改写"}, headers=Harness.headers(hr10))
        row = self._assert_rejected_unchanged(r, rid, status="draft")
        self.assertNotEqual(row.summary, "越权改写")
        self.assertEqual(self._event_count(), before)

    def test_scoped_hr_submit_out_of_department_rejected(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        rid = self._review(self.other)
        before = self._event_count()
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(hr10))
        self._assert_rejected_unchanged(r, rid, status="draft")
        self.assertEqual(self._event_count(), before)

    def test_scoped_hr_confirm_out_of_department_rejected(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        rid = self._review(self.other, submit=True)
        before = self._event_count()
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(hr10))
        row = self._assert_rejected_unchanged(r, rid, status="submitted")
        self.assertIsNone(row.grid_config_version)
        self.assertIsNone(row.reviewer_hr_id)
        self.assertEqual(self._event_count(), before)

    def test_empty_scope_hr_denied_by_default(self):
        # dept 范围但无部门无授权 → 空范围一律拒绝
        hr_none = self._hr("hr_none", scope="dept")
        rid = self._review(self.emp)
        r = self.h.client.patch(f"/api/inventory/reviews/{rid}",
                                json={"summary": "x"}, headers=Harness.headers(hr_none))
        self.assertEqual(r.status_code, 403, r.text)

    # ---- 本部门与全量授权按契约成功

    def test_scoped_hr_in_department_full_chain_succeeds(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        rid = self._review(self.emp)
        r = self.h.client.patch(f"/api/inventory/reviews/{rid}",
                                json={"summary": "范围内更新"}, headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._review_row(rid).status, "confirmed")

    def test_full_scope_hr_cross_department_succeeds(self):
        hr_all = self._hr("hr_all", scope="all")
        rid = self._review(self.other)
        r = self.h.client.patch(f"/api/inventory/reviews/{rid}",
                                json={"summary": "全量授权更新"}, headers=Harness.headers(hr_all))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/submit", headers=Harness.headers(hr_all))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/confirm", headers=Harness.headers(hr_all))
        self.assertEqual(r.status_code, 200, r.text)

    # ---- 同域其余按 ID 写入/导出入口

    def test_scoped_hr_pool_nominate_and_decide_out_of_scope(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        r = self.h.client.post("/api/inventory/pools",
                               json={"employee_user_id": self.other.id, "pool_type": "high_potential"},
                               headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 403, r.text)
        r = self.h.client.post("/api/inventory/pools",
                               json={"employee_user_id": self.other.id, "pool_type": "high_potential"},
                               headers=Harness.headers(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["data"]["id"]
        r = self.h.client.post(f"/api/inventory/pools/{pid}/decide",
                               json={"status": "approved"}, headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 403, r.text)
        # 本部门提名/决定正常
        r = self.h.client.post("/api/inventory/pools",
                               json={"employee_user_id": self.emp.id, "pool_type": "high_potential"},
                               headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)
        pid10 = r.json()["data"]["id"]
        r = self.h.client.post(f"/api/inventory/pools/{pid10}/decide",
                               json={"status": "approved"}, headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)

    def test_scoped_hr_list_pools_only_own_scope(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        for emp in (self.emp, self.other):
            r = self.h.client.post("/api/inventory/pools",
                                   json={"employee_user_id": emp.id, "pool_type": "high_potential"},
                                   headers=Harness.headers(self.admin))
            self.assertEqual(r.status_code, 200, r.text)
        r = self.h.client.get("/api/inventory/pools", headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json()["data"]
        self.assertEqual([p["employee_user_id"] for p in rows], [self.emp.id])

    def test_scoped_hr_dispute_respond_out_of_scope(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        rid = self._review(self.other)
        r = self.h.client.post(f"/api/inventory/reviews/{rid}/disputes",
                               json={"content": "本人异议"}, headers=Harness.headers(self.other))
        self.assertEqual(r.status_code, 200, r.text)
        did = r.json()["data"]["id"]
        r = self.h.client.post(f"/api/inventory/disputes/{did}/respond",
                               json={"status": "resolved", "response": "越权处理"},
                               headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 403, r.text)

    def test_scoped_hr_idp_out_of_scope_and_self_service(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        rid_other = self._review(self.other)
        r = self.h.client.put(f"/api/inventory/reviews/{rid_other}/idp",
                              json={"content_json": {"goal": "x"}}, headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 403, r.text)
        rid_own = self._review(self.emp)
        r = self.h.client.put(f"/api/inventory/reviews/{rid_own}/idp",
                              json={"content_json": {"goal": "x"}}, headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)
        # 员工本人仍可维护自己的发展计划（既有行级规则不变）
        r = self.h.client.put(f"/api/inventory/reviews/{rid_own}/idp",
                              json={"content_json": {"goal": "self"}}, headers=Harness.headers(self.emp))
        self.assertEqual(r.status_code, 200, r.text)

    def test_scoped_hr_export_only_own_scope(self):
        hr10 = self._hr("hr10", scope="dept", department_id=10)
        self._review(self.emp, confirm=True)
        self._review(self.other, confirm=True)
        r = self.h.client.get("/api/inventory/summary/export", headers=Harness.headers(hr10))
        self.assertEqual(r.status_code, 200, r.text)
        total_line = [ln for ln in r.text.splitlines() if ln.startswith("total,,")][0]
        self.assertEqual(total_line, "total,,1")
        r = self.h.client.get("/api/inventory/summary/export", headers=Harness.headers(self.admin))
        total_line = [ln for ln in r.text.splitlines() if ln.startswith("total,,")][0]
        self.assertEqual(total_line, "total,,2")


if __name__ == "__main__":
    unittest.main()