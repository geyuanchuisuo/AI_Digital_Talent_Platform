"""A06 角色/职责门禁（P0 子单元）：sys_user_roles 聚合角色 + 管理侧/导出/任务入口越权拒绝。

边界（见 docs/updates/A/20261110-11xx-A06角色门禁.md）：
- 新增基于 `sys_user_roles` 聚合的 `get_role_codes` / `account_is_management`，统一替代散落的
  `can_view_all_persons` 与 inventory `ADMIN_ROLES` 字符串判断；修复 system_admin 被误拒漏洞。
- 不改动既有数据范围（can_view_all_persons）消费语义；不做部门行级/字段级（无 users.department_id）。
- 使用真实隔离库（与全量同库）；角色种子已由 init_db 写入，测试内对所需 code 做幂等补建。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.auth.scope import (
    MANAGEMENT_ROLES,
    ROLE_CODES,
    account_is_management,
    get_role_codes,
    require_management_guard,
)
from app.database import SessionLocal
from app.models import SysRole, SysUserRole, User
from app.service.auth_service import hash_password


def _ensure_role(db, code: str, name: str, scope: str) -> int:
    row = db.execute(select(SysRole).where(SysRole.code == code)).scalar_one_or_none()
    if row is None:
        row = SysRole(code=code, name=name, data_scope=scope, status="active")
        db.add(row)
        db.flush()
    return row.id


def _make_user(db, username: str, role: str, bind_role_code: str | None) -> int:
    row = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if row is None:
        row = User(
            username=username,
            password_hash=hash_password("Test@123456"),
            display_name=username,
            role=role,
            is_active=1,
        )
        db.add(row)
        db.flush()
    if bind_role_code is not None:
        rid = _ensure_role(db, bind_role_code, bind_role_code, "all" if bind_role_code in ("system_admin", "hr") else "dept")
        exists = db.execute(
            select(SysUserRole).where(
                SysUserRole.user_id == row.id, SysUserRole.role_id == rid
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(SysUserRole(user_id=row.id, role_id=rid))
    return row.id


# --------------------------------------------------------------------------- #
# 角色聚合（纯逻辑 + 真实库）
# --------------------------------------------------------------------------- #

def test_role_codes_constants():
    assert set(ROLE_CODES) == {"system_admin", "hr", "dept_manager", "trainer", "employee", "candidate"}
    assert MANAGEMENT_ROLES == ("system_admin", "hr")


def test_role_codes_fallback_for_legacy_hr_and_personal():
    with SessionLocal() as db:
        hr_id = _make_user(db, "a06_hr_legacy", "hr", bind_role_code=None)
        db.commit()
        assert get_role_codes(db, hr_id) == ("hr",)

        personal_id = _make_user(db, "a06_personal_legacy", "personal", bind_role_code=None)
        db.commit()
        assert get_role_codes(db, personal_id) == ("personal",)


def test_role_codes_unknown_role_maps_to_personal():
    with SessionLocal() as db:
        unknown_id = _make_user(db, "a06_unknown", "guest", bind_role_code=None)
        db.commit()
        assert get_role_codes(db, unknown_id) == ("personal",)


def test_role_codes_aggregates_sys_user_roles_over_users_role():
    with SessionLocal() as db:
        # users.role="employee" 但绑定 dept_manager 职责 —— 聚合应取 dept_manager
        dm_id = _make_user(db, "a06_deptmgr", "employee", bind_role_code="dept_manager")
        db.commit()
        assert get_role_codes(db, dm_id) == ("dept_manager",)


def test_account_is_management_true_for_system_admin_and_hr():
    with SessionLocal() as db:
        sa_id = _make_user(db, "a06_sysadmin", "system_admin", bind_role_code="system_admin")
        hr_id = _make_user(db, "a06_hr2", "hr", bind_role_code="hr")
        db.commit()
        assert account_is_management(db, User(id=sa_id)) is True
        assert account_is_management(db, User(id=hr_id)) is True


def test_account_is_management_false_for_dept_manager_trainer_employee_candidate_personal():
    with SessionLocal() as db:
        dm_id = _make_user(db, "a06_dm2", "employee", bind_role_code="dept_manager")
        tr_id = _make_user(db, "a06_tr2", "employee", bind_role_code="trainer")
        em_id = _make_user(db, "a06_em2", "employee", bind_role_code="employee")
        ca_id = _make_user(db, "a06_ca2", "personal", bind_role_code="candidate")
        db.commit()
        for uid in (dm_id, tr_id, em_id, ca_id):
            assert account_is_management(db, User(id=uid)) is False


def test_require_management_guard_raises_for_non_management():
    with SessionLocal() as db:
        dm_id = _make_user(db, "a06_dm3", "employee", bind_role_code="dept_manager")
        db.commit()
        with pytest.raises(Exception):  # BizError(403)
            require_management_guard(User(id=dm_id))


# --------------------------------------------------------------------------- #
# 入口越权拒绝（TestClient 端到端）
# --------------------------------------------------------------------------- #

@pytest.fixture()
def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _login_token(client, username: str, password: str = "Test@123456") -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def test_digital_tasks_endpoint_denies_dept_manager(_client):
    with SessionLocal() as db:
        _make_user(db, "a06_dm_ep", "employee", bind_role_code="dept_manager")
        db.commit()
    token = _login_token(_client, "a06_dm_ep")
    resp = _client.get("/api/workflows/digital/tasks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_digital_tasks_endpoint_allows_hr(_client):
    # 复用种子 hr 账号（users.role=hr 且无 sys_user_roles 时回退为 ("hr",) → 管理侧）
    token = _login_token(_client, "hr", "hr123456")
    resp = _client.get("/api/workflows/digital/tasks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_inventory_summary_export_denies_dept_manager(_client):
    with SessionLocal() as db:
        _make_user(db, "a06_dm_exp", "employee", bind_role_code="dept_manager")
        db.commit()
    token = _login_token(_client, "a06_dm_exp")
    resp = _client.get("/api/inventory/summary/export", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
