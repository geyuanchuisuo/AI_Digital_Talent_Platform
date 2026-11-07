"""组织范围授权（V2.6 §3.1 冻结口径：跨部门授权需记录依据和期限）。

`user_scope_grants`：授权记录表——HR/组织管理员按职责授予跨部门范围，
D 的管理层问数与 B 的数据过滤据此判定组织范围。与既有 users 表的映射
兼容策略在公共契约冻结（design §8.3 的 sys_user_roles 体系为后续演进）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import MYSQL_TABLE_OPTIONS, PKMixin, PK_TYPE, UpdatedAtMixin

SCOPE_TYPES = ("department", "project", "all")  # 授权范围类型（V2.6：部门/项目/全量）


class UserScopeGrant(PKMixin, UpdatedAtMixin, Base):
    """跨部门/跨项目授权记录：granter 授予 grantee 对 scope 的数据范围。"""

    __tablename__ = "user_scope_grants"
    __table_args__ = (
        Index("idx_grant_grantee", "grantee_user_id"),
        Index("idx_grant_scope", "scope_type", "scope_ref_id"),
        MYSQL_TABLE_OPTIONS,
    )

    granter_user_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    grantee_user_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_ref_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)  # department/project 的 id；all 为 NULL
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 期限（V2.6：记录依据和期限）
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
