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

    ⏳ `person_id` 绑定列（B 契约 2026-11-07：复用 `talent_profiles.id`，同
    `PK_TYPE` 整数、可空、个人账号唯一）已按 B 契约设计，但**延后落地**：
    C 的 `tests/C/c_harness.py` 手写 users SQLite DDL 需先同步该列（见
    docs/requests/A/ 请求），避免集成破坏 C 测试夹具。加列时必须走迁移链。
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uk_username"),
        MYSQL_TABLE_OPTIONS,
    )

    username: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="hr", server_default=str_default("hr")
    )
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
