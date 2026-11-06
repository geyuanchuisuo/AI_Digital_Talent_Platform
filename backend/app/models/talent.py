"""人才画像域：`talent_profiles` / `talent_tags` / `talent_duplicates`（数据库设计 v1.2 §5）。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, DECIMAL, Index, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    JSONDict,
    JSONList,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    CreatedAtMixin,
    UpdatedAtMixin,
    str_default,
)


class TalentProfile(PKMixin, UpdatedAtMixin, Base):
    """§5.1 画像主表。

    - `degree` 存《算法与判定规则》v1.2 §1 的归一化字面值（含「高中中专」）。
    - `vector_status` 三态 pending/synced/failed，半写口径见文档 §5.1。
    - 软删除只在本表用 deleted_at；默认查询需过滤。
    - `is_manual_edited` 布尔列已取消，由 manual_edited_fields 是否非空推导。
    """

    __tablename__ = "talent_profiles"
    __table_args__ = (
        Index("idx_name", "name"),
        Index("idx_phone", "phone"),
        Index("idx_email", "email"),
        Index("idx_degree", "degree"),
        Index("idx_work_years", "work_years"),
        Index("idx_updated_at", "updated_at"),
        Index("idx_deleted_at", "deleted_at"),
        MYSQL_TABLE_OPTIONS,
    )

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    birth_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(50), nullable=True)
    degree: Mapped[str | None] = mapped_column(String(20), nullable=True)
    work_years: Mapped[Decimal | None] = mapped_column(DECIMAL(4, 1), nullable=True)
    current_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expected_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    skills_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    tags_json: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    ability_scores_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    profile_text_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    vector_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    vector_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="resume", server_default=str_default("resume")
    )
    manual_edited_fields: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)


class TalentTag(PKMixin, CreatedAtMixin, Base):
    """§5.2 标签关联表：uk_profile_tag 保证同事务重建幂等。"""

    __tablename__ = "talent_tags"
    __table_args__ = (
        UniqueConstraint("talent_profile_id", "tag", name="uk_profile_tag"),
        Index("idx_tag", "tag"),
        Index("idx_tag_type_tag", "tag_type", "tag"),
        MYSQL_TABLE_OPTIONS,
    )

    talent_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    tag: Mapped[str] = mapped_column(String(100), nullable=False)
    tag_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="general", server_default=str_default("general")
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="llm", server_default=str_default("llm")
    )


class TalentDuplicate(PKMixin, CreatedAtMixin, Base):
    """§5.3 查重待确认。

    `parsed_resume_id` UNIQUE：同一解析结果只能有一条查重记录，重试再次命中时更新原记录。
    `profile_b_id` 在 pending/merge 场景恒为 NULL，仅 keep 同步建画像后回填。
    """

    __tablename__ = "talent_duplicates"
    __table_args__ = (
        UniqueConstraint("parsed_resume_id", name="uk_parsed_resume"),
        Index("idx_dup_profile_a", "profile_a_id"),
        Index("idx_dup_profile_b", "profile_b_id"),
        MYSQL_TABLE_OPTIONS,
    )

    profile_a_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    profile_b_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    parsed_resume_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    matched_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    resolution_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
