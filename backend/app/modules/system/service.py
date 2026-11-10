"""RBAC 判定服务（8c）：sys_roles/sys_user_roles/sys_role_permissions 查询与判定。

阶段口径（V2.6 §3.1）：一人多职责——`users.role` 简值保留为登录分发主职责，
细粒度职责由 sys_user_roles 多对多承载；服务端判定入口 `get_account_scope`
演进为「多职责聚合」（阶段 1 三值兼容：无 sys_user_roles 记录时按 users.role
回退，不破坏既有登录/守卫）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import SysRole, SysRolePermission, SysUserRole, User
from app.modules.system.models import ACTIONS, DATA_SCOPES


def list_user_roles(db: Session, user_id: int) -> list[SysRole]:
    """账号的全部启用角色（多职责）；按 id 升序稳定返回。"""
    rows = db.execute(
        select(SysRole)
        .join(SysUserRole, SysUserRole.role_id == SysRole.id)
        .where(SysUserRole.user_id == user_id)
        .where(SysRole.status == "active")
        .order_by(SysRole.id.asc())
    ).scalars().all()
    return list(rows)


def widest_data_scope(db: Session, user_id: int) -> str:
    """账号的最宽数据范围（all > dept > self）；无角色记录回退 users.role 简值。

    阶段 1 回退口径：users.role == "hr" → all；其余 → self（deny-by-default，
    与 core/auth/scope.py 的未知角色口径一致）。
    """
    rows = list_user_roles(db, user_id)
    if not rows:
        user = db.get(User, user_id)
        return "all" if user is not None and user.role == "hr" else "self"
    if any(r.data_scope == "all" for r in rows):
        return "all"
    if any(r.data_scope == "dept" for r in rows):
        return "dept"
    return "self"


def user_has_permission(db: Session, user_id: int, *, action: str, menu_id: int | None = None) -> bool:
    """动作权限判定：角色上有该 action（指定 menu_id 时精确匹配）即放行。

    `action` 不在白名单（ACTIONS）时返回 False（deny-by-default）。
    """
    if action not in ACTIONS:
        return False
    stmt = (
        select(func.count())
        .select_from(SysRolePermission)
        .join(SysUserRole, SysUserRole.role_id == SysRolePermission.role_id)
        .where(SysUserRole.user_id == user_id)
        .where(SysRolePermission.action == action)
    )
    if menu_id is not None:
        stmt = stmt.where(SysRolePermission.menu_id == menu_id)
    return (db.execute(stmt).scalar_one() or 0) > 0


def validate_grant_scope(data_scope: str) -> bool:
    return data_scope in DATA_SCOPES


def list_user_role_codes(db: Session, user_id: int) -> tuple[str, ...]:
    """账号的全部启用角色 code（多职责；供 AccountScope.roles 与前端展示）。"""
    rows = db.execute(
        select(SysRole.code)
        .join(SysUserRole, SysUserRole.role_id == SysRole.id)
        .where(SysUserRole.user_id == user_id)
        .where(SysRole.status == "active")
        .order_by(SysRole.id.asc())
    ).scalars().all()
    return tuple(rows)
