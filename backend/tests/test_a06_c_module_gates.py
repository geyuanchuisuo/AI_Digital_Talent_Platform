"""A06-5：assessments / interviews 内联 ADMIN_ROLES 行级判定迁移（真实 MySQL）。

背景：C 归属的 `assessments/service.py` 与 `interviews/ai_router.py` 仍用
`ADMIN_ROLES = {"admin", "hr"}` 字符串判断，与 A06-1 统一的角色门禁口径不一致：
- `system_admin`（users.role=system_admin + sys_user_roles 绑定）会被**误拒**；
- 非标准 "admin" 值会被**误放行**。
本单元把管理侧判定统一到 `scope.account_is_management`（sys_user_roles 聚合），
保留「本人 or 管理」行级语义与原有错误消息。

验证点：
- `assessments.service.is_admin`：system_admin / legacy hr → True；dept_manager / personal → False。
- `require_admin`：非管理抛 BizError(403)、管理不抛（错误消息保持"仅管理员可执行该操作"）。
- HTTP `POST /api/ai-interviews/sessions` 端到端：system_admin 200（旧代码 403）、
  legacy hr 200、dept_manager 403、personal 403、未登录 401。
- 常量 `ADMIN_ROLES` 仍在（兼容外部 import），但不再用于判定。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.errors import BizError
from app.database import SessionLocal
from app.models import User
from app.modules.assessments.service import ADMIN_ROLES, is_admin, require_admin
from app.modules.interviews.ai_models import AiInterviewEvent, AiInterviewSession
from app.modules.system.models import SysRole, SysUserRole
from app.service.auth_service import hash_password

pytestmark = pytest.mark.integration

PWD = "a06c-pass-123456"


def _login(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def env():
    tag = uuid.uuid4().hex[:8]
    session_ids: list[int] = []
    with SessionLocal() as db:
        sysadmin = User(username=f"a06c_sys_{tag}", password_hash=hash_password(PWD), display_name="A06C 系统管理员", role="system_admin", is_active=1)
        hr = User(username=f"a06c_hr_{tag}", password_hash=hash_password(PWD), display_name="A06C HR", role="hr", is_active=1)
        deptmgr = User(username=f"a06c_dm_{tag}", password_hash=hash_password(PWD), display_name="A06C 部门经理", role="employee", is_active=1)
        candidate = User(username=f"a06c_ca_{tag}", password_hash=hash_password(PWD), display_name="A06C 候选人", role="personal", is_active=1)
        db.add_all([sysadmin, hr, deptmgr, candidate])
        db.flush()

        admin_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "system_admin"))
        dm_role_id = db.scalar(select(SysRole.id).where(SysRole.code == "dept_manager"))
        db.add(SysUserRole(user_id=sysadmin.id, role_id=admin_role_id))
        db.add(SysUserRole(user_id=deptmgr.id, role_id=dm_role_id))
        db.commit()
        ids = {
            "sysadmin": sysadmin.id, "hr": hr.id, "deptmgr": deptmgr.id, "candidate": candidate.id,
        }

    from app.main import app

    with TestClient(app) as client:
        ctx = {
            "client": client, "tag": tag, "ids": ids, "session_ids": session_ids,
            "sysadmin_token": _login(client, f"a06c_sys_{tag}", PWD),
            "hr_token": _login(client, f"a06c_hr_{tag}", PWD),
            "deptmgr_token": _login(client, f"a06c_dm_{tag}", PWD),
            "candidate_token": _login(client, f"a06c_ca_{tag}", PWD),
        }
        yield ctx

    with SessionLocal() as db:
        if session_ids:
            db.execute(delete(AiInterviewEvent).where(AiInterviewEvent.session_id.in_(session_ids)))
            db.execute(delete(AiInterviewSession).where(AiInterviewSession.id.in_(session_ids)))
        db.execute(delete(SysUserRole).where(SysUserRole.user_id.in_(list(ids.values()))))
        for uid in ids.values():
            u = db.get(User, uid)
            if u:
                db.delete(u)
        db.commit()


# --------------------------------------------------------------------------- #
# 共享判定函数（assessments.service.is_admin，被 interviews / training 复用）
# --------------------------------------------------------------------------- #

def test_legacy_constant_kept_for_compat_but_not_used():
    assert ADMIN_ROLES == frozenset({"admin", "hr"})


def test_is_admin_true_for_system_admin_and_legacy_hr(env):
    with SessionLocal() as db:
        sa = db.get(User, env["ids"]["sysadmin"])
        hr = db.get(User, env["ids"]["hr"])
        assert is_admin(sa) is True   # 旧代码会误拒（role=system_admin 不在 {"admin","hr"}）
        assert is_admin(hr) is True


def test_is_admin_false_for_dept_manager_and_personal(env):
    with SessionLocal() as db:
        dm = db.get(User, env["ids"]["deptmgr"])
        ca = db.get(User, env["ids"]["candidate"])
        assert is_admin(dm) is False
        assert is_admin(ca) is False


def test_is_admin_does_not_trust_nonstandard_admin_string():
    """非标准 role="admin" 无 sys_user_roles 绑定 → deny-by-default，不再被误放行。"""
    with SessionLocal() as db:
        u = User(username="a06c_fake_admin", password_hash=hash_password(PWD), display_name="fake", role="admin", is_active=1)
        db.add(u)
        db.commit()
        try:
            assert is_admin(u) is False
        finally:
            db.delete(u)
            db.commit()


def test_require_admin_raises_403_for_non_management_with_original_message(env):
    with SessionLocal() as db:
        dm = db.get(User, env["ids"]["deptmgr"])
        with pytest.raises(BizError) as exc:
            require_admin(dm)
        assert exc.value.status_code == 403
        assert "仅管理员可执行该操作" in str(exc.value)


def test_require_admin_passes_for_system_admin(env):
    with SessionLocal() as db:
        sa = db.get(User, env["ids"]["sysadmin"])
        require_admin(sa)  # 不抛异常


# --------------------------------------------------------------------------- #
# HTTP 端到端：AI 面试间管理门
# --------------------------------------------------------------------------- #

def test_create_session_allows_system_admin(env):
    c = env["client"]
    r = c.post("/api/ai-interviews/sessions", headers=_hdr(env["sysadmin_token"]), json={})
    assert r.status_code == 200, r.text
    env["session_ids"].append(r.json()["data"]["id"])


def test_create_session_allows_legacy_hr(env):
    c = env["client"]
    r = c.post("/api/ai-interviews/sessions", headers=_hdr(env["hr_token"]), json={})
    assert r.status_code == 200, r.text
    env["session_ids"].append(r.json()["data"]["id"])


def test_create_session_denies_dept_manager(env):
    c = env["client"]
    r = c.post("/api/ai-interviews/sessions", headers=_hdr(env["deptmgr_token"]), json={})
    assert r.status_code == 403, r.text


def test_create_session_denies_personal(env):
    c = env["client"]
    r = c.post("/api/ai-interviews/sessions", headers=_hdr(env["candidate_token"]), json={})
    assert r.status_code == 403, r.text


def test_create_session_unauthenticated_401(env):
    r = env["client"].post("/api/ai-interviews/sessions", json={})
    assert r.status_code == 401, r.text


def test_list_sessions_row_level_for_personal(env):
    """非管理侧只能看到自己的 session（行级语义保留）。"""
    c = env["client"]
    r = c.get("/api/ai-interviews/sessions", headers=_hdr(env["candidate_token"]))
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    # 前面管理侧创建的 session 的 candidate_user_id 为 None，个人账号不应看到
    assert all(s["candidate_user_id"] == env["ids"]["candidate"] for s in items)
