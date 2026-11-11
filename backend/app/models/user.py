"""用户域：`users` / `sys_settings`（数据库设计 v1.2 §3）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    BOOL_TYPE,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    UpdatedAtMixin,
    num_default,
    str_default,
)


class User(PKMixin, UpdatedAtMixin, Base):
    """§3.1 users：登录名唯一；role 为二期 RBAC 扩展点。

    `person_id` 绑定 B 的人才主档（B 契约 2026-11-07：直接复用
    `talent_profiles.id`，同 `PK_TYPE` 整数；可空＝未绑定档案的管理员账号，
    个人账号唯一绑定）。A 只绑定与消费，不建第二套主档、不按姓名联表。
    2026-11-07 落地：迁移 `A_0002_users_person_id`；C 的 c_harness 手写
    SQLite DDL 已同步该列（配合变更，见 A 交接记录）。
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uk_username"),
        UniqueConstraint("person_id", name="uk_user_person_id"),
        MYSQL_TABLE_OPTIONS,
    )

    username: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="hr", server_default=str_default("hr")
    )
    person_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    data_scope_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="legacy", server_default=str_default("legacy")
    )
    department_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    is_active: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=1, server_default=num_default(1)
    )
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SysSetting(PKMixin, UpdatedAtMixin, Base):
    """§3.2 sys_settings：运行时可配项；一期唯一键 task_concurrency（int，1–5）。

    与 .env 的关系：`TASK_CONCURRENCY` 只是 init_db.py 首次种子值与本表缺行时的兜底；
    本表有值时以本表为准，重启后仍保持（运行配置 v1.3）。
    """

    __tablename__ = "sys_settings"
    __table_args__ = (
        UniqueConstraint("setting_key", name="uk_setting_key"),
        MYSQL_TABLE_OPTIONS,
    )

    setting_key: Mapped[str] = mapped_column(String(50), nullable=False)
    setting_value: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="int", server_default=str_default("int")
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
