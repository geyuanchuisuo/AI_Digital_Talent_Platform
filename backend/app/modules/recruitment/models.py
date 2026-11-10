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
    CreatedAtMixin,
    JSONDict,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    UpdatedAtMixin,
    num_default,
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

# ---- A11 岗位/人才需求审批链（需求细化 §6.11 步骤 1 + Master 执行表 A11）----
# 业务生命周期 status（open/in_progress/fulfilled/closed）与业务审批状态**分离**：
# 需求文档 §202 明确「业务审批状态不得与技术任务状态混用」，此处沿用同一原则，
# 不让 status 的推进替代审批。审批只作用于某个冻结版本（approval_version）。
DEMAND_APPROVAL_STATUSES = ("draft", "pending", "approved", "rejected", "withdrawn")
DEMAND_APPROVAL_ACTIONS = ("submit", "approve", "reject", "return", "withdraw", "revision")
DEMAND_DECISIONS = ("approve", "reject", "return")

#: HR 发起（§6.11 步骤 1「HR 发起」）；system_admin 为受控兜底，仍受四眼约束。
DEMAND_SUBMIT_ROLES = ("system_admin", "hr")
#: 业务负责人审批；system_admin 作为兜底管理员，但「提交人不可自批」由用户 id 强制。
DEMAND_APPROVER_ROLES = ("dept_manager", "system_admin")
#: 审批记录只读：发起方、审批方与管理员可追溯。
DEMAND_RECORD_READ_ROLES = ("system_admin", "hr", "dept_manager")

#: 提交前必须完整的核心字段（§6.11 步骤 1「必填项完整且审批通过」）。
#: 组织/岗位归属至少落一个锚点，避免无归属需求进入可匹配状态。
DEMAND_REQUIRED_FIELDS = ("position_name", "headcount", "priority")
DEMAND_ANCHOR_FIELDS = ("project_id", "project_group_id", "job_profile_id")


def demand_is_matchable(row) -> bool:
    """只有「已审批」且审批版本 == 当前版本的需求才进入可匹配状态（§6.11 步骤 1/5）。

    审批通过后任何内容变更都会使 approval_version +1 并回到 draft，旧审批自动失效。
    """
    return (
        getattr(row, "approval_status", None) == "approved"
        and getattr(row, "approved_version", None) is not None
        and row.approved_version == row.approval_version
    )


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

    # A11：业务审批状态与冻结版本。历史行由迁移补 server_default，语义为 draft/1。
    approval_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )
    approval_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=num_default(1)
    )
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DemandApprovalRecord(PKMixin, CreatedAtMixin, Base):
    """A11 审批留痕：每次提交/审批/退回/撤回/版本失效一行，只追加不修改。

    `snapshot_json` 是决策时刻需求业务字段的**值拷贝**（A11-v1），用于回答
    「当时批的是哪一版、哪些字段」；需求后续变更不回写历史记录。无物理外键，
    demand_id 一致性由 service 层同事务保证（§1 约定）。
    """

    __tablename__ = "demand_approval_records"
    __table_args__ = (
        Index("idx_demand_approval_demand", "demand_id", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    demand_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    demand_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    actor_roles: Mapped[str] = mapped_column(String(150), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)


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
    后才对外发布。

    A08：`input_snapshot_json` 是**创建时一次性冻结的申请输入值拷贝**（岗位/JD
    条件、人才档案、简历原件与解析结果），不是外键引用。岗位、JD 或档案后续
    变更一律不得回写本列；历史申请据此复现当时的评分依据（需求 §73/§201/
    §280）。`input_snapshot_at` 记录冻结时刻；迁移前的历史行两列均可为 NULL。
    面试/邀约等过程引用由 C 归属 `interview_rounds.application_id` 等外键
    承载，不重复冻进本快照。
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
    # A08 申请输入快照：创建时冻结，之后只读；NULL 表示迁移前的历史申请。
    input_snapshot_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    input_snapshot_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
