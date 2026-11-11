"""A06-4：系统管理域 CRUD 路由端到端（真实 MySQL，不外呼 LLM/Embedding）。

验证点（对应执行表 A06-4「系统管理域 CRUD router 并接 require_roles」）：
- 门禁：`require_roles("system_admin")` 在真实 API 边界生效——
  未登录 401；hr / personal 一律 403；system_admin 放行。
- sysadmin 可：部门 CRUD 往返、角色读、权限矩阵写入、账号↔角色绑定/解绑、操作日志只读。
- 全量清理：所有临时账号 / 角色 / 绑定 / 权限 / 部门 / 菜单在 teardown 直连删除，不留孤儿。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import User
from app.modules.system.models import (
    SysDepartment,
    SysMenu,
    SysRole,
    SysRolePermission,
    SysUserRole,
)
from app.service.auth_service import hash_password

pytestmark = pytest.mark.integration

PWD = "a06-pass-123456"


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
        # 三个临时账号：sysadmin 绑 system_admin 角色；hr 绑 hr 角色；personal 无绑定
        sysadmin = User(username=f"a06_sys_{tag}", password_hash=hash_password(PWD), display_name="A06 系统管理员", role="system_admin", is_active=1)
        hr = User(username=f"a06_hr_{tag}", password_hash=hash_password(PWD), display_name="A06 HR", role="hr", is_active=1)
        personal = User(username=f"a06_per_{tag}", password_hash=hash_password(PWD), display_name="A06 个人", role="personal", is_active=1)
        db.add_all([sysadmin, hr, personal])
        db.flush()
        admin_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "system_admin"))
        hr_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "hr"))
        db.add(SysUserRole(user_id=sysadmin.id, role_id=admin_role_id))
        db.add(SysUserRole(user_id=hr.id, role_id=hr_role_id))
        db.commit()
        ids = {"sysadmin": sysadmin.id, "hr": hr.id, "personal": personal.id}

    from app.main import app

    with TestClient(app) as client:
        ctx = {
            "client": client,
            "tag": tag,
            "ids": ids,
            "sysadmin_token": _login(client, f"a06_sys_{tag}", PWD),
            "hr_token": _login(client, f"a06_hr_{tag}", PWD),
            "personal_token": _login(client, f"a06_per_{tag}", PWD),
        }
        yield ctx

    # teardown：直连清理所有临时数据与绑定，避免遗留孤儿影响后续运行
    with SessionLocal() as db:
        db.execute(
            delete(SysRolePermission).where(
                SysRolePermission.role_id.in_(select(SysRole.id).where(SysRole.code.like(f"a06_{tag}_%")))
            )
        )
        db.execute(delete(SysRole).where(SysRole.code.like(f"a06_{tag}_%")))
        db.execute(delete(SysUserRole).where(SysUserRole.user_id.in_([ids["sysadmin"], ids["hr"], ids["personal"]])))
        db.execute(delete(SysDepartment).where(SysDepartment.name.like(f"a06_{tag}_%")))
        db.execute(delete(SysMenu).where(SysMenu.name.like(f"a06_{tag}_%")))
        for uid in (ids["sysadmin"], ids["hr"], ids["personal"]):
            u = db.get(User, uid)
            if u:
                db.delete(u)
        db.commit()


def test_unauthenticated_rejected(env):
    r = env["client"].get("/api/system/roles")
    assert r.status_code == 401, r.text


def test_hr_and_personal_forbidden(env):
    for token in (env["hr_token"], env["personal_token"]):
        r = env["client"].get("/api/system/roles", headers=_hdr(token))
        assert r.status_code == 403, r.text


def test_sysadmin_lists_roles(env):
    r = env["client"].get("/api/system/roles", headers=_hdr(env["sysadmin_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    codes = {item["code"] for item in body["data"]["items"]}
    assert "system_admin" in codes and "hr" in codes


def test_sysadmin_department_crud_roundtrip(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    tag = env["tag"]
    # create
    name = f"a06_{tag}_dept"
    r = c.post("/api/system/departments", headers=h, json={"name": name, "leader": "张三", "sort": 1})
    assert r.status_code == 200, r.text
    dept_id = r.json()["data"]["id"]
    # get
    r = c.get(f"/api/system/departments/{dept_id}", headers=h)
    assert r.status_code == 200 and r.json()["data"]["name"] == name
    # update
    r = c.put(f"/api/system/departments/{dept_id}", headers=h, json={"leader": "李四"})
    assert r.status_code == 200 and r.json()["data"]["leader"] == "李四"
    # duplicate name 409
    r = c.post("/api/system/departments", headers=h, json={"name": name})
    assert r.status_code == 409, r.text
    # delete
    r = c.delete(f"/api/system/departments/{dept_id}", headers=h)
    assert r.status_code == 200 and r.json()["data"]["deleted"] is True
    # gone
    r = c.get(f"/api/system/departments/{dept_id}", headers=h)
    assert r.status_code == 404, r.text


def test_sysadmin_role_permissions_matrix(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    tag = env["tag"]
    # 建一个临时角色用于权限矩阵验证
    r = c.post("/api/system/roles", headers=h, json={"code": f"a06_{tag}_role", "name": f"a06_{tag}_role", "data_scope": "dept"})
    assert r.status_code == 200, r.text
    role_id = r.json()["data"]["id"]
    try:
        # 写入权限矩阵
        r = c.put(
            f"/api/system/roles/{role_id}/permissions",
            headers=h,
            json={"permissions": [{"menu_id": None, "action": "view", "data_scope": "all"}]},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]) == 1
        # 非法 action 400
        r = c.put(
            f"/api/system/roles/{role_id}/permissions",
            headers=h,
            json={"permissions": [{"menu_id": None, "action": "explode", "data_scope": "all"}]},
        )
        assert r.status_code == 400, r.text
    finally:
        c.delete(f"/api/system/roles/{role_id}", headers=h)


def test_sysadmin_bind_unbind_user(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    tag = env["tag"]
    # 临时角色承载绑定
    r = c.post("/api/system/roles", headers=h, json={"code": f"a06_{tag}_b", "name": f"a06_{tag}_b", "data_scope": "self"})
    assert r.status_code == 200, r.text
    role_id = r.json()["data"]["id"]
    try:
        hr_id = env["ids"]["hr"]
        r = c.post(f"/api/system/roles/{role_id}/users", headers=h, json={"user_id": hr_id})
        assert r.status_code == 200, r.text
        r = c.get(f"/api/system/roles/{role_id}/users", headers=h)
        assert any(item["user_id"] == hr_id for item in r.json()["data"])
        # 重复绑定 409
        r = c.post(f"/api/system/roles/{role_id}/users", headers=h, json={"user_id": hr_id})
        assert r.status_code == 409, r.text
        # 不存在账号 404
        r = c.post(f"/api/system/roles/{role_id}/users", headers=h, json={"user_id": 99999999})
        assert r.status_code == 404, r.text
        r = c.delete(f"/api/system/roles/{role_id}/users/{hr_id}", headers=h)
        assert r.status_code == 200, r.text
    finally:
        c.delete(f"/api/system/roles/{role_id}", headers=h)


def test_sysadmin_reads_operation_logs(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    r = c.get("/api/system/operation-logs", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    # 前面的部门/角色写操作应至少产生一条审计
    assert body["data"]["total"] >= 1


@pytest.mark.parametrize("mode_before_unbind", ["legacy", "scoped"])
def test_last_binding_revocation_persists_scoped_mode(env, mode_before_unbind):
    from app.core.auth.scope import get_account_scope, get_role_codes, resolve_data_scope

    c, h = env["client"], _hdr(env["sysadmin_token"])
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        target = User(username=f"a06_revoke_{tag}", password_hash=hash_password(PWD), role="hr")
        role = SysRole(code=f"a06_revoke_{tag}", name="Synthetic limited HR", data_scope="self")
        db.add_all([target, role])
        db.commit()
        uid, rid = target.id, role.id
        assert target.data_scope_mode == "legacy"
    try:
        response = c.post(f"/api/system/roles/{rid}/users", headers=h, json={"user_id": uid})
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            target = db.get(User, uid)
            assert target.data_scope_mode == "scoped"
            assert resolve_data_scope(db, target) == "self"
            # Covers old bindings removed without any prior authorization read.
            target.data_scope_mode = mode_before_unbind
            db.commit()
        response = c.delete(f"/api/system/roles/{rid}/users/{uid}", headers=h)
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            target = db.get(User, uid)
            assert target.data_scope_mode == "scoped"
            assert resolve_data_scope(db, target) == "self"
            assert get_role_codes(db, uid) == ("personal",)
            assert not get_account_scope(target).can_view_all_persons
    finally:
        with SessionLocal() as db:
            db.execute(delete(SysUserRole).where(SysUserRole.user_id == uid))
            db.execute(delete(SysRole).where(SysRole.id == rid))
            db.execute(delete(User).where(User.id == uid))
            db.commit()
