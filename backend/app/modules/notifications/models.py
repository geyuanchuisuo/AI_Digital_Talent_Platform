"""通知域 ORM（T-D03）：notifications 表 + F09 封闭清单事件枚举。

字段依据：docs/modules/D/通知契约.md v1.0 §2（schema 冻结稿）。无物理外键；
收件人/操作人引用 users.id，由生产方保证存在。唯一约束 `idempotency_key`
是「同一事件重复触发不重复通知」（F09 验收）的数据库级保证。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    JSONDict,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    CreatedAtMixin,
)

# ---- F09 封闭清单：12 类业务事件 → 14 个 event_type（契约 v1.0 §1）----
# 拆分说明：「Agent 运行失败/需人工介入」= run_failed + needs_human；
#          「必修逾期/延期」= training.overdue + training.extension_decided。
EVENT_TYPES: tuple[str, ...] = (
    "recruitment.stage_changed",      # A   招聘阶段变化
    "interview.invited",              # A   面试邀约
    "interview.rescheduled",          # A   面试改期
    "interview.cancelled",            # A   面试取消
    "course.assigned",                # C   课程指派
    "course.enrollment_reviewed",     # C   报名审批结果
    "course.waitlist_promoted",       # C   候补递补
    "training.due_reminder",          # C   必修课到期提醒
    "training.overdue",               # C   必修课逾期
    "training.extension_decided",     # C   必修课延期
    "workflow.approval_pending",      # D   Agent 审批待办
    "workflow.run_failed",            # D   Agent 运行失败
    "workflow.needs_human",           # D   Agent 需人工介入
    "talent.gap_alert",               # D   人才缺口预警（第 12 类）
)

# 多收件人事件（同一业务事实发员工+经理/管理员+负责人）：幂等键须带收件人，
# 否则第二位收件人会被唯一约束拦掉（契约 v1.1 修订）
MULTI_RECIPIENT_EVENTS: frozenset[str] = frozenset(
    {
        "training.due_reminder",
        "training.overdue",
        "training.extension_decided",
        "workflow.approval_pending",
        "workflow.run_failed",
        "workflow.needs_human",
        "talent.gap_alert",
    }
)

REF_TYPES: tuple[str, ...] = (
    "application",             # 招聘申请
    "interview_round",         # 面试轮次
    "training_plan_item",      # 课程指派
    "training_enrollment",     # 报名
    "training_session",        # 课程期次
    "training_learning_record",  # 学习记录
    "workflow_run",            # 工作流运行
    "project_demand",          # 人才需求（缺口预警）
)

STATUSES: tuple[str, ...] = ("pending", "sent", "failed", "read")


class Notification(PKMixin, CreatedAtMixin, Base):
    """站内通知（契约 v1.0 §2 冻结 schema；无 updated_at，状态时间戳用 sent_at/read_at）。"""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_notifications_idempotency"),
        Index("idx_notifications_recipient", "recipient_user_id", "status"),
        Index("idx_notifications_ref", "event_type", "ref_type", "ref_id"),
        MYSQL_TABLE_OPTIONS,
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    recipient_user_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
