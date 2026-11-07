"""C AI 面试间域 ORM：在线考试与结构化问答组织记录、AI 评判。

音视频/情绪推断涉及个人信息，须先完成告知授权与隐私评审后启用；
本期不实现音视频采集，仅记录文本问答与 AI 评分。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import JSONDict, MYSQL_TABLE_OPTIONS, PKMixin, str_default
from app.modules.assessments.models import CTimestampMixin


class AiInterviewSession(PKMixin, CTimestampMixin, Base):
    __tablename__ = "ai_interview_sessions"
    __table_args__ = MYSQL_TABLE_OPTIONS

    candidate_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    application_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(nullable=True)
    overall_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AiInterviewQuestion(PKMixin, CTimestampMixin, Base):
    __tablename__ = "ai_interview_questions"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_no", name="uk_ai_q_session_seq"),
        MYSQL_TABLE_OPTIONS,
    )

    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="structured", server_default=str_default("structured")
    )
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_score: Mapped[float | None] = mapped_column(nullable=True)
    ai_feedback_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )


class AiInterviewEvent(PKMixin, CTimestampMixin, Base):
    __tablename__ = "ai_interview_events"
    __table_args__ = MYSQL_TABLE_OPTIONS

    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
