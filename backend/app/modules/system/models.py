"""系统管理域 ORM（8b，V2.6 冻结口径）：design 数据库表设计 §8.3 的 sys_* 表体系。

- RBAC：sys_roles.data_scope（all/dept/self）+ sys_role_permissions（menu×action×data_scope）；
- 菜单树：sys_menus（parent_id/catalog/menu/button）；
- 审计：sys_operation_logs（全量操作日志，F09）。
- 与既有 users 表的关系：users 为本仓库既有账号表（含 person_id/role 简值）；
  sys_user_roles 建立账号↔角色多对多（一人多职责，V2.6 §3.1），映射兼容策略
  在公共契约冻结。无物理外键，一致性由 service 层保证（§1 约定）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    UpdatedAtMixin,
    num_default,
    str_default,
)

DATA_SCOPES = ("all", "dept", "self")
MENU_TYPES = ("catalog", "menu", "button")
ACTIONS = ("view", "create", "edit", "delete", "export")


class SysDepartment(PKMixin, UpdatedAtMixin, Base):
    """部门树（design §8.3 sys_departments）。"""

    __tablename__ = "sys_departments"
    __table_args__ = (
        Index("idx_dept_parent", "parent_id"),
        MYSQL_TABLE_OPTIONS,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    leader: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=num_default(0))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=str_default("active")
    )


class SysRole(PKMixin, UpdatedAtMixin, Base):
    """角色（data_scope 三值：all/dept/self——design §8.3；服务端判定入口）。"""

    __tablename__ = "sys_roles"
    __table_args__ = (
        UniqueConstraint("code", name="uk_role_code"),
        MYSQL_TABLE_OPTIONS,
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    data_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="self", server_default=str_default("self")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=str_default("active")
    )


class SysUserRole(PKMixin, Base):
    """账号↔角色多对多（一人多职责，V2.6 §3.1）。"""

    __tablename__ = "sys_user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uk_user_role"),
        Index("idx_ur_user", "user_id"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    role_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)


class SysRolePermission(PKMixin, Base):
    """角色×菜单×动作×数据范围（design §8.3 sys_role_permissions）。"""

    __tablename__ = "sys_role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "menu_id", "action", name="uk_role_menu_action"),
        Index("idx_rp_role", "role_id"),
        MYSQL_TABLE_OPTIONS,
    )

    role_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    menu_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False, default="view")
    data_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="self", server_default=str_default("self")
    )


class SysMenu(PKMixin, UpdatedAtMixin, Base):
    """菜单树（catalog/menu/button 三类；design §8.3 sys_menus）。"""

    __tablename__ = "sys_menus"
    __table_args__ = (
        Index("idx_menu_parent", "parent_id"),
        MYSQL_TABLE_OPTIONS,
    )

    parent_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    permission_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    menu_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="menu", server_default=str_default("menu")
    )
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=num_default(0))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=str_default("active")
    )


class SysOperationLog(PKMixin, Base):
    """全量操作审计日志（F09；只追加，不更新）。"""

    __tablename__ = "sys_operation_logs"
    __table_args__ = (
        Index("idx_oplog_user", "user_id"),
        Index("idx_oplog_time", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    module: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    params: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
