"""C 培训成长域 ORM（契约 §2）：课程、计划、计划项、计划版本、学习记录、成长事件。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import JSONDict, JSONList, MYSQL_TABLE_OPTIONS, PKMixin, str_default
from app.modules.assessments.models import CTimestampMixin


class TrainingCourse(PKMixin, CTimestampMixin, Base):
    """真实课程目录；材料仅接受 http/https 链接，不伪造资源。"""

    __tablename__ = "training_courses"
    __table_args__ = MYSQL_TABLE_OPTIONS

    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_url: Mapped[str] = mapped_column(String(500), nullable=False)
    dimension_names_json: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=str_default("active")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingPlan(PKMixin, CTimestampMixin, Base):
    __tablename__ = "training_plans"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default=str_default("manual")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )
    input_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
    gap_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingPlanItem(PKMixin, CTimestampMixin, Base):
    """计划项绑定当时的课程快照，后续改课程不回写历史版本。"""

    __tablename__ = "training_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "plan_version", "course_id", name="uk_plan_version_course"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    dimension_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TrainingPlanVersion(PKMixin, CTimestampMixin, Base):
    __tablename__ = "training_plan_versions"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uk_plan_version"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingLearningRecord(PKMixin, CTimestampMixin, Base):
    """员工自报学习状态；完成不代表验证学时。"""

    __tablename__ = "training_learning_records"
    __table_args__ = (
        UniqueConstraint("plan_item_id", "employee_user_id", name="uk_learning_item_employee"),
        MYSQL_TABLE_OPTIONS,
    )

    plan_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_started", server_default=str_default("not_started")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_self_reported: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )


class TrainingGrowthEvent(PKMixin, CTimestampMixin, Base):
    """追加式成长事件：测评完成、培训分配、课程完成。"""

    __tablename__ = "training_growth_events"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
