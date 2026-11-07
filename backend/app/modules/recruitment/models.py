"""招聘域 ORM（M3，V2.6 冻结口径）：项目/项目群/人才需求、JD 投放、岗位申请与五阶段映射。

字段依据：design/数据库表设计 §9（project_demands：project_name/project_group/
position/headcount/expected_start_date/priority/remark/status）与统一需求 V2.6 F01
（多岗位独立申请、五阶段对外映射、终态结果类别经 HR 确认发布）。全部无物理外键，
一致性由 service 层同事务保证（§1 约定）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    JSONDict,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    UpdatedAtMixin,
    str_default,
)

# ---- 内部阶段枚举（冻结基线；对外五阶段映射见 EXTERNAL_STAGE_MAP）----
INTERNAL_STAGES = (
    "new",            # 新建/待筛选 → 对外「已提交」
    "screening",      # 筛选中 → 对外「筛选中」
    "matched",        # 匹配完成/复核中 → 对外「筛选中」
    "invited",        # 已邀约 → 对外「待面试」
    "interviewing",   # 面试中 → 对外「待面试」
    "deciding",       # 面试完成待决策 → 对外「待决定」
    "offer_accepted",  # 终态：录用 → 对外「已结束」（结果类别=录用）
    "rejected",       # 终态：未通过 → 对外「已结束」（结果类别=未录用）
    "withdrawn",      # 终态：候选人撤销 → 对外「已结束」（未录用）
    "job_closed",     # 终态：岗位关闭 → 对外「已结束」（未录用）
)

# 内部阶段 → 对外五阶段（V2.6 F01 已确认映射，随内部流程自动更新）
EXTERNAL_STAGES = ("submitted", "screening", "interviewing", "deciding", "closed")
EXTERNAL_STAGE_MAP: dict[str, str] = {
    "new": "submitted",
    "screening": "screening",
    "matched": "screening",
    "invited": "interviewing",
    "interviewing": "interviewing",
    "deciding": "deciding",
    "offer_accepted": "closed",
    "rejected": "closed",
    "withdrawn": "closed",
    "job_closed": "closed",
}

# 终态集合与结果类别（V2.6：终态显示结果类别「录用/未录用」，经 HR 确认后发布）
TERMINAL_STAGES = ("offer_accepted", "rejected", "withdrawn", "job_closed")
RESULT_CATEGORIES = ("hired", "not_hired")

DEMAND_PRIORITIES = ("high", "medium", "low")
DEMAND_STATUSES = ("open", "in_progress", "fulfilled", "closed")
PUBLICATION_STATUSES = ("draft", "published", "withdrawn")


def external_stage_of(internal_stage: str) -> str:
    """内部阶段 → 对外阶段映射；未知内部阶段返回 submitted（保守不外露后续进展）。"""
    return EXTERNAL_STAGE_MAP.get(internal_stage, "submitted")


class ProjectGroup(PKMixin, UpdatedAtMixin, Base):
    """项目群（design §9 project_groups；V2.6 F01 项目群归属）。"""

    __tablename__ = "project_groups"
    __table_args__ = MYSQL_TABLE_OPTIONS

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=str_default("active")
    )
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)


class Project(PKMixin, UpdatedAtMixin, Base):
    """项目（归属项目群；design §9 projects）。"""

    __tablename__ = "projects"
    __table_args__ = MYSQL_TABLE_OPTIONS

    project_group_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=str_default("active")
    )
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)


class ProjectDemand(PKMixin, UpdatedAtMixin, Base):
    """项目人才需求（design §9：project_name/project_group/position/headcount/
    expected_start_date/priority/remark/status；V2.6 F01 增岗位条件关联）。"""

    __tablename__ = "project_demands"
    __table_args__ = (
        Index("idx_demand_project", "project_id"),
        Index("idx_demand_job", "job_profile_id"),
        MYSQL_TABLE_OPTIONS,
    )

    project_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    project_group_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    job_profile_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    position_name: Mapped[str] = mapped_column(String(120), nullable=False)
    headcount: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expected_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default=str_default("medium")
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default=str_default("open")
    )
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)


class JdPublication(PKMixin, UpdatedAtMixin, Base):
    """JD 生成投放（V2.6 F01 新增：草稿经授权确认后投放，渠道/版本留痕可撤回）。"""

    __tablename__ = "jd_publications"
    __table_args__ = (
        Index("idx_jd_pub_job", "job_profile_id"),
        MYSQL_TABLE_OPTIONS,
    )

    job_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    job_profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    content_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )
    published_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    withdrawn_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Application(PKMixin, UpdatedAtMixin, Base):
    """岗位申请（V2.6 F01/D-26：同人多岗位独立申请，各自快照与进度）。

    internal_stage 内部状态；external_stage 按 EXTERNAL_STAGE_MAP 随内部流程
    自动更新（人工修正留痕）；终态的 result_category（录用/未录用）经 HR 确认
    后才对外发布。简历快照以 resume_file_id 引用原件。
    """

    __tablename__ = "applications"
    __table_args__ = (
        Index("idx_application_talent", "talent_profile_id"),
        Index("idx_application_job", "job_profile_id"),
        Index("idx_application_external", "external_stage"),
        MYSQL_TABLE_OPTIONS,
    )

    talent_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    job_profile_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    job_profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resume_file_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    internal_stage: Mapped[str] = mapped_column(
        String(30), nullable=False, default="new", server_default=str_default("new")
    )
    external_stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default="submitted", server_default=str_default("submitted")
    )
    result_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    result_confirmed_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    stage_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stage_updated_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    stage_updated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
