"""A06-2：users.department_id + 部门行级过滤（真实 MySQL，不外呼 LLM/Embedding）。

验证点（对应执行表 A06-2「补 users.department_id + 部门行级过滤」）：
- 数据范围解析：resolve_data_scope 按角色 data_scope 取最宽松口径；
  get_visible_department_ids 对 all 返回 None、对 dept 返回 (department_id,)。
- 账号花名册 GET /api/system/accounts：
  * 未登录 401；hr / personal 一律 403（门禁 require_roles(system_admin, dept_manager)）；
  * system_admin 看全部账号；dept_manager 仅看本部门（含跨部门授权）。
- 部门指派 PUT /api/system/accounts/{id}/department：仅 system_admin 可改；dept_manager 403。
- 动态性：system_admin 把某账号改到 dept_manager 所属部门后，该账号立即可被 dept_manager 看到。
- 全量清理：临时账号 / 部门在 teardown 直连删除，不留孤儿。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import User
from app.modules.system.models import SysDepartment, SysRole, SysUserRole
from app.service.auth_service import hash_password

pytestmark = pytest.mark.integration

PWD = "a06d-pass-123456"


def _login(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def env():
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        sysadmin = User(username=f"a06d_sys_{tag}", password_hash=hash_password(PWD), display_name="A06D 系统管理员", role="system_admin", is_active=1)
        deptmgr = User(username=f"a06d_dm_{tag}", password_hash=hash_password(PWD), display_name="A06D 部门经理", role="employee", is_active=1)
        hr = User(username=f"a06d_hr_{tag}", password_hash=hash_password(PWD), display_name="A06D HR", role="hr", is_active=1)
        personal = User(username=f"a06d_per_{tag}", password_hash=hash_password(PWD), display_name="A06D 个人", role="personal", is_active=1)
        db.add_all([sysadmin, deptmgr, hr, personal])
        db.flush()

        admin_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "system_admin"))
        dm_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "dept_manager"))
        hr_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "hr"))
        db.add(SysUserRole(user_id=sysadmin.id, role_id=admin_role_id))
        db.add(SysUserRole(user_id=deptmgr.id, role_id=dm_role_id))
        db.add(SysUserRole(user_id=hr.id, role_id=hr_role_id))
        db.flush()

        # 两个部门
        dept_a = SysDepartment(name=f"a06d_{tag}_A", status="active")
        dept_b = SysDepartment(name=f"a06d_{tag}_B", status="active")
        db.add_all([dept_a, dept_b])
        db.flush()
        dept_a_id, dept_b_id = dept_a.id, dept_b.id

        # deptmgr 属于部门 A
        deptmgr.department_id = dept_a_id

        # 三个测试账号分布在不同部门
        u_a1 = User(username=f"a06d_ua1_{tag}", password_hash=hash_password(PWD), display_name="A 部门员工1", role="employee", is_active=1, department_id=dept_a_id)
        u_a2 = User(username=f"a06d_ua2_{tag}", password_hash=hash_password(PWD), display_name="A 部门员工2", role="employee", is_active=1, department_id=dept_a_id)
        u_b1 = User(username=f"a06d_ub1_{tag}", password_hash=hash_password(PWD), display_name="B 部门员工1", role="employee", is_active=1, department_id=dept_b_id)
        db.add_all([u_a1, u_a2, u_b1])
        db.commit()
        ids = {
            "sysadmin": sysadmin.id, "deptmgr": deptmgr.id, "hr": hr.id, "personal": personal.id,
            "u_a1": u_a1.id, "u_a2": u_a2.id, "u_b1": u_b1.id,
            "dept_a": dept_a_id, "dept_b": dept_b_id,
        }

    from app.main import app

    with TestClient(app) as client:
        ctx = {
            "client": client, "tag": tag, "ids": ids,
            "sysadmin_token": _login(client, f"a06d_sys_{tag}", PWD),
            "deptmgr_token": _login(client, f"a06d_dm_{tag}", PWD),
            "hr_token": _login(client, f"a06d_hr_{tag}", PWD),
            "personal_token": _login(client, f"a06d_per_{tag}", PWD),
        }
        yield ctx

    # teardown：直连清理临时数据与绑定
    with SessionLocal() as db:
        for uid in ids.values():
            u = db.get(User, uid)
            if u:
                db.delete(u)
        db.execute(delete(SysUserRole).where(SysUserRole.user_id.in_(list(ids.values()))))
        db.execute(delete(SysDepartment).where(SysDepartment.name.like(f"a06d_{tag}_%")))
        db.commit()


# --------------------------------------------------------------------------- #
# 数据范围解析（单元测试 + 真实库）
# --------------------------------------------------------------------------- #

def test_resolve_data_scope_all_for_system_admin(env):
    from app.core.auth.scope import get_visible_department_ids, resolve_data_scope

    with SessionLocal() as db:
        assert resolve_data_scope(db, User(id=env["ids"]["sysadmin"])) == "all"
        assert get_visible_department_ids(db, User(id=env["ids"]["sysadmin"])) is None


def test_resolve_data_scope_dept_for_dept_manager(env):
    from app.core.auth.scope import get_visible_department_ids, resolve_data_scope

    with SessionLocal() as db:
        deptmgr = db.get(User, env["ids"]["deptmgr"])
        assert resolve_data_scope(db, deptmgr) == "dept"
        assert get_visible_department_ids(db, deptmgr) == (env["ids"]["dept_a"],)


def test_resolve_data_scope_fallback_legacy_hr_is_all():
    from app.core.auth.scope import resolve_data_scope

    with SessionLocal() as db:
        u = User(username="a06d_legacy_hr", password_hash=hash_password(PWD), display_name="legacy", role="hr", is_active=1)
        db.add(u)
        db.commit()
        try:
            assert resolve_data_scope(db, User(id=u.id)) == "all"
        finally:
            db.delete(u)
            db.commit()


# --------------------------------------------------------------------------- #
# 账号花名册部门行级过滤（端到端）
# --------------------------------------------------------------------------- #

def test_unauthenticated_rejected(env):
    r = env["client"].get("/api/system/accounts")
    assert r.status_code == 401, r.text


def test_hr_and_personal_forbidden(env):
    for token in (env["hr_token"], env["personal_token"]):
        r = env["client"].get("/api/system/accounts", headers=_hdr(token))
        assert r.status_code == 403, r.text


def test_sysadmin_sees_all_departments(env):
    c = env["client"]
    r = c.get("/api/system/accounts", headers=_hdr(env["sysadmin_token"]), params={"page_size": 200})
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    ids_in_result = {it["id"] for it in items}
    # 两个部门的员工都在结果里
    assert env["ids"]["u_a1"] in ids_in_result
    assert env["ids"]["u_b1"] in ids_in_result


def test_dept_manager_sees_only_own_department(env):
    c = env["client"]
    r = c.get("/api/system/accounts", headers=_hdr(env["deptmgr_token"]), params={"page_size": 200})
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    ids_in_result = {it["id"] for it in items}
    # 本部门（A）可见
    assert env["ids"]["u_a1"] in ids_in_result
    assert env["ids"]["u_a2"] in ids_in_result
    assert env["ids"]["deptmgr"] in ids_in_result
    # 非本部门（B）不可见
    assert env["ids"]["u_b1"] not in ids_in_result


def test_dept_manager_cannot_reassign_department(env):
    c = env["client"]
    h = _hdr(env["deptmgr_token"])
    r = c.put(f"/api/system/accounts/{env['ids']['u_a1']}/department", headers=h, json={"department_id": env["ids"]["dept_b"]})
    assert r.status_code == 403, r.text


def test_sysadmin_reassign_department_then_dept_manager_sees(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    # 把 B 部门员工改到 A 部门
    r = c.put(f"/api/system/accounts/{env['ids']['u_b1']}/department", headers=h, json={"department_id": env["ids"]["dept_a"]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["department_id"] == env["ids"]["dept_a"]
    # dept_manager 现在能看到了
    r = c.get("/api/system/accounts", headers=_hdr(env["deptmgr_token"]), params={"page_size": 200})
    assert r.status_code == 200, r.text
    ids_in_result = {it["id"] for it in r.json()["data"]["items"]}
    assert env["ids"]["u_b1"] in ids_in_result
    # 还原（改回 B），保持 teardown 一致
    r = c.put(f"/api/system/accounts/{env['ids']['u_b1']}/department", headers=h, json={"department_id": env["ids"]["dept_b"]})
    assert r.status_code == 200, r.text


def test_sysadmin_reassign_unknown_department_400(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    r = c.put(f"/api/system/accounts/{env['ids']['u_a1']}/department", headers=h, json={"department_id": 99999999})
    assert r.status_code == 400, r.text


def test_sysadmin_reassign_unknown_user_404(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    r = c.put("/api/system/accounts/99999999/department", headers=h, json={"department_id": env["ids"]["dept_a"]})
    assert r.status_code == 404, r.text
