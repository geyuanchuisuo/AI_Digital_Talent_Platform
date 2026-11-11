"""L1/L2 单元测试 · RBAC 判定与操作审计（8c）。

多职责聚合、data_scope 最宽口径、动作白名单 deny-by-default、纯关联表写入、
审计写入失败不影响业务（返回 None）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

import app.models  # noqa: F401 - 先触发完整注册，避免部分初始化环（见 A→C registry 分析）

from app.models import User
from app.modules.system.models import (
    ACTIONS,
    DATA_SCOPES,
    SysOperationLog,
    SysRole,
    SysUserRole,
)
from app.modules.system.service import (
    list_user_roles,
    user_has_permission,
    validate_grant_scope,
    widest_data_scope,
)
from app.modules.system.audit_service import record_operation


def test_actions_and_scopes_whitelist():
    assert set(ACTIONS) == {"view", "create", "edit", "delete", "export"}
    assert set(DATA_SCOPES) == {"all", "dept", "self"}


def test_validate_grant_scope():
    assert validate_grant_scope("all") is True
    assert validate_grant_scope("everything") is False


def test_list_user_roles_empty():
    from app.database import SessionLocal

    with SessionLocal() as db:
        assert list_user_roles(db, user_id=9999) == []


def test_widest_data_scope_fallback_by_users_role():
    """无 sys_user_roles 记录时按 users.role 回退：hr→all，其他→self。

    用自建 rbac_ 前缀 hr 用户定位（REQ-D-20261108-052000）：勿假设 seed hr 的
    自增 id——测试库重建后 id 会漂移（hr 曾从 1 漂到 68），user_id=1 不存在时
    回退落到 'self' 造成误报。
    """
    from app.database import SessionLocal

    hr = _make_user(role="hr")
    with SessionLocal() as db:
        assert widest_data_scope(db, user_id=hr.id) == "all"
        personal = _make_user(role="personal")
        assert widest_data_scope(db, user_id=personal.id) == "self"


def _make_user(role: str):
    from app.models import User
    from app.database import SessionLocal

    with SessionLocal() as db:
        user = User(username=f"rbac_{role}_{uuid.uuid4().hex[:8]}", password_hash="x", role=role, is_active=1)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_widest_data_scope_multi_role():
    """多职责聚合：一条 self + 一条 all → 最宽 all。测试数据带 rbac_ 前缀并 finally 清理。"""
    from app.database import SessionLocal

    created_ids: list[int] = []
    try:
        with SessionLocal() as db:
            user = _make_user(role="personal")
            created_ids.append(user.id)
            role_self = SysRole(code=f"r_self_{uuid.uuid4().hex[:8]}", name="self 角色", data_scope="self", status="active")
            role_all = SysRole(code=f"r_all_{uuid.uuid4().hex[:8]}", name="all 角色", data_scope="all", status="active")
            db.add_all([role_self, role_all])
            db.flush()
            created_ids.extend([role_self.id, role_all.id])
            db.add(SysUserRole(user_id=user.id, role_id=role_self.id))
            db.add(SysUserRole(user_id=user.id, role_id=role_all.id))
            db.commit()
            uid = user.id

        with SessionLocal() as db:
            assert widest_data_scope(db, user_id=uid) == "all"
            names = [r.name for r in list_user_roles(db, user_id=uid)]
            assert sorted(names) == ["all 角色", "self 角色"]  # 聚合内容正确；顺序按 id 升序
    finally:
        from sqlalchemy import delete

        with SessionLocal() as db:
            db.execute(delete(SysUserRole).where(SysUserRole.user_id.in_(created_ids) | SysUserRole.role_id.in_(created_ids)))
            db.execute(delete(SysRole).where(SysRole.id.in_(created_ids)))
            db.execute(delete(User).where(User.id.in_(created_ids)))
            db.commit()


def test_permission_deny_by_default():
    from app.database import SessionLocal

    with SessionLocal() as db:
        assert user_has_permission(db, user_id=1, action="destroy") is False  # 白名单外


def test_audit_write_failure_returns_none():
    """审计写入失败不抛出（返回 None），不影响业务响应。"""
    import app.modules.system.audit_service as audit

    class BoomSession:
        def add(self, obj):
            raise RuntimeError("boom")

        def rollback(self):
            pass

    result = audit.record_operation(
        BoomSession(),  # type: ignore[arg-type]
        user_id=1, username="u", method="POST", path="/x", status="success",
    )
    assert result is None


def test_record_operation_ok():
    from app.database import SessionLocal

    with SessionLocal() as db:
        row = record_operation(
            db, user_id=1, username="hr", method="PATCH", path="/api/recruitment/demands/1",
            status="success", duration_ms=12, ip="127.0.0.1", module="recruitment", action="update",
        )
        assert row is not None and row.id is not None
        assert row.path.startswith("/api/recruitment")
