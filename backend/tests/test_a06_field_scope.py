"""A06-3：字段级权限白名单 + 序列化裁剪（真实 MySQL，不外呼 LLM/Embedding）。

验证点（对应执行表 A06-3「字段级权限白名单 + 序列化裁剪」）：
- 硬底线：password_hash 等 SENSITIVE_FIELDS 任何角色、任何白名单都不可出现在响应。
- 策略：system_admin → None（全部非敏感列）；dept_manager → ACCOUNT_BASE_FIELDS；
  未知角色 / 未知资源 → ()（deny-by-default，一个字段都不给）。
- 端到端：dept_manager 的账号花名册每条记录仅含基础字段；system_admin 含审计/绑定类字段；
  PUT 部门指派的返回也按同一策略裁剪。
- 与 A06-2 组合：字段裁剪叠加在部门行级过滤之上，不互相破坏。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.auth.scope import (
    ACCOUNT_BASE_FIELDS,
    FIELD_POLICIES,
    SENSITIVE_FIELDS,
    filter_columns,
    resolve_visible_fields,
)
from app.database import SessionLocal
from app.models import User
from app.modules.system.models import SysDepartment, SysRole, SysUserRole
from app.service.auth_service import hash_password

pytestmark = pytest.mark.integration

PWD = "a06f-pass-123456"
ACCOUNT_AUDIT_FIELDS = ("person_id", "last_login_at", "created_at", "updated_at")


def _login(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def env():
    tag = uuid.uuid4().hex[:8]
    user_ids: list[int] = []
    with SessionLocal() as db:
        sysadmin = User(username=f"a06f_sys_{tag}", password_hash=hash_password(PWD), display_name="A06F 系统管理员", role="system_admin", is_active=1)
        deptmgr = User(username=f"a06f_dm_{tag}", password_hash=hash_password(PWD), display_name="A06F 部门经理", role="employee", is_active=1)
        u1 = User(username=f"a06f_u1_{tag}", password_hash=hash_password(PWD), display_name="A06F 员工1", role="employee", is_active=1)
        u2 = User(username=f"a06f_u2_{tag}", password_hash=hash_password(PWD), display_name="A06F 员工2", role="employee", is_active=1)
        db.add_all([sysadmin, deptmgr, u1, u2])
        db.flush()

        admin_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "system_admin"))
        dm_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "dept_manager"))
        db.add(SysUserRole(user_id=sysadmin.id, role_id=admin_role_id))
        db.add(SysUserRole(user_id=deptmgr.id, role_id=dm_role_id))
        db.flush()

        dept = SysDepartment(name=f"a06f_{tag}_A", status="active")
        db.add(dept)
        db.flush()
        dept_id = dept.id
        deptmgr.department_id = dept_id
        u1.department_id = dept_id
        u2.department_id = dept_id
        db.commit()
        user_ids = [sysadmin.id, deptmgr.id, u1.id, u2.id]

    from app.main import app

    with TestClient(app) as client:
        ctx = {
            "client": client, "tag": tag, "user_ids": user_ids, "dept_id": dept_id,
            "sysadmin_token": _login(client, f"a06f_sys_{tag}", PWD),
            "deptmgr_token": _login(client, f"a06f_dm_{tag}", PWD),
        }
        yield ctx

    # teardown：只用用户 id（不要把部门 id 混进来误删同号用户）
    with SessionLocal() as db:
        for uid in user_ids:
            u = db.get(User, uid)
            if u:
                db.delete(u)
        db.execute(delete(SysUserRole).where(SysUserRole.user_id.in_(user_ids)))
        db.execute(delete(SysDepartment).where(SysDepartment.name.like(f"a06f_{tag}_%")))
        db.commit()


# --------------------------------------------------------------------------- #
# 策略与序列化纯函数
# --------------------------------------------------------------------------- #

def test_sensitive_fields_are_hard_denylist():
    assert "password_hash" in SENSITIVE_FIELDS
    assert {"password", "token", "secret", "api_key"} <= set(SENSITIVE_FIELDS)
    assert "accounts" in FIELD_POLICIES
    assert FIELD_POLICIES["accounts"]["system_admin"] is None
    assert FIELD_POLICIES["accounts"]["dept_manager"] == ACCOUNT_BASE_FIELDS


def test_filter_columns_none_returns_all_non_sensitive():
    cols = ("id", "username", "password_hash", "person_id", "created_at")
    assert filter_columns(cols, None) == ("id", "username", "person_id", "created_at")


def test_filter_columns_whitelist_intersects_and_never_leaks_secrets():
    cols = ("id", "username", "password_hash", "person_id", "department_id")
    visible = ("id", "username", "department_id", "password_hash", "not_a_column")
    # 即使白名单里写了 password_hash，也必须被硬底线剔除
    assert filter_columns(cols, visible) == ("id", "username", "department_id")


def test_filter_columns_empty_whitelist_denies_all():
    cols = ("id", "username", "password_hash")
    assert filter_columns(cols, ()) == ()


def test_resolve_visible_fields_system_admin_gets_all(env):
    with SessionLocal() as db:
        u = db.get(User, env["user_ids"][0])
        assert resolve_visible_fields(db, u, "accounts") is None


def test_resolve_visible_fields_dept_manager_gets_base(env):
    with SessionLocal() as db:
        u = db.get(User, env["user_ids"][1])
        assert resolve_visible_fields(db, u, "accounts") == ACCOUNT_BASE_FIELDS


def test_resolve_visible_fields_deny_by_default(env):
    with SessionLocal() as db:
        # 普通 employee 账号（无 system_admin / dept_manager 绑定）→ 空集
        u = db.get(User, env["user_ids"][2])
        assert resolve_visible_fields(db, u, "accounts") == ()
        # 未知资源 → 空集（即便 system_admin）
        sa = db.get(User, env["user_ids"][0])
        assert resolve_visible_fields(db, sa, "unknown_resource") == ()


# --------------------------------------------------------------------------- #
# 端到端：字段裁剪叠加在部门行级过滤之上
# --------------------------------------------------------------------------- #

def test_dept_manager_account_list_has_only_base_fields(env):
    c = env["client"]
    r = c.get("/api/system/accounts", headers=_hdr(env["deptmgr_token"]), params={"page_size": 200})
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) >= 3
    for item in items:
        assert set(item) == set(ACCOUNT_BASE_FIELDS), item
        assert "password_hash" not in item
        for field in ACCOUNT_AUDIT_FIELDS:
            assert field not in item


def test_sysadmin_account_list_has_audit_fields_but_no_password(env):
    c = env["client"]
    r = c.get("/api/system/accounts", headers=_hdr(env["sysadmin_token"]), params={"page_size": 200})
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert items
    for item in items:
        assert "password_hash" not in item
        assert "person_id" in item
        assert "created_at" in item


def test_dept_manager_still_row_filtered_after_field_trim(env):
    """字段裁剪不破坏 A06-2 的部门行级过滤：本部门 3 人可见，department_id 全为部门 A。"""
    c = env["client"]
    r = c.get("/api/system/accounts", headers=_hdr(env["deptmgr_token"]), params={"page_size": 200})
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    own = [i for i in items if i["id"] in set(env["user_ids"])]
    assert len(own) == 3  # deptmgr + u1 + u2 在部门 A；sysadmin 无部门不可见
    assert all(i["department_id"] == env["dept_id"] for i in own)


def test_put_department_response_respects_field_policy(env):
    c = env["client"]
    h = _hdr(env["sysadmin_token"])
    r = c.put(
        f"/api/system/accounts/{env['user_ids'][2]}/department",
        headers=h,
        json={"department_id": env["dept_id"]},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["department_id"] == env["dept_id"]
    assert "password_hash" not in data
    # system_admin 为全量非敏感列，审计字段可见
    assert "person_id" in data


def test_gate_unchanged_unauthenticated_rejected(env):
    r = env["client"].get("/api/system/accounts")
    assert r.status_code == 401, r.text
