"""C 人工面试域 ORM（契约 §2）：轮次、反馈、变更事件。

面试不计算总分、不自动推进招聘；反馈仅管理员与获授权面试人可见。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import JSONDict, JSONList, MYSQL_TABLE_OPTIONS, PKMixin, str_default
from app.modules.assessments.models import CTimestampMixin


class InterviewRound(PKMixin, CTimestampMixin, Base):
    __tablename__ = "interview_rounds"
    __table_args__ = MYSQL_TABLE_OPTIONS

    candidate_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    round_name: Mapped[str] = mapped_column(String(50), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="onsite", server_default=str_default("onsite")
    )
    location_or_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    interviewer_user_ids_json: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InterviewFeedback(PKMixin, CTimestampMixin, Base):
    """每轮一条结构化反馈；无面试总分字段。"""

    __tablename__ = "interview_feedback"
    __table_args__ = (
        UniqueConstraint("round_id", name="uk_feedback_round"),
        MYSQL_TABLE_OPTIONS,
    )

    round_id: Mapped[int] = mapped_column(Integer, nullable=False)
    interviewer_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[str | None] = mapped_column(Text, nullable=True)
    weaknesses: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str] = mapped_column(
        String(20), nullable=False, default="hold", server_default=str_default("hold")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class InterviewRoundEvent(PKMixin, CTimestampMixin, Base):
    """轮次变更审计：创建/改期/完成/取消/反馈保存。"""

    __tablename__ = "interview_round_events"
    __table_args__ = MYSQL_TABLE_OPTIONS

    round_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
