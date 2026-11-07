"""C 人才盘点域 ORM（F10）：绩效+潜力→九宫格、人才池、异议、发展计划、审计。

九宫格：绩效 × 潜力各 1–5 档，cell = (performance, potential)；
绩效取最近 HR 确认周期，潜力经理初评 + HR 审核。员工不可见内部评级/九宫格/人才池。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import JSONDict, MYSQL_TABLE_OPTIONS, PKMixin, str_default
from app.modules.assessments.models import CTimestampMixin


class InventoryReview(PKMixin, CTimestampMixin, Base):
    """一次盘点复核：绩效/潜力→九宫格。"""

    __tablename__ = "inventory_reviews"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycle_name: Mapped[str] = mapped_column(String(50), nullable=False)
    review_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="annual", server_default=str_default("annual")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )
    performance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    performance_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", server_default=str_default("none")
    )
    potential_manager_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    potential_hr_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grid_cell: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reviewer_manager_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_hr_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)


class InventoryTalentPool(PKMixin, CTimestampMixin, Base):
    """人才池进出审批。"""

    __tablename__ = "inventory_talent_pools"
    __table_args__ = MYSQL_TABLE_OPTIONS

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    pool_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="nominated", server_default=str_default("nominated")
    )
    nominated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class InventoryDispute(PKMixin, CTimestampMixin, Base):
    """员工对盘点结果的异议。"""

    __tablename__ = "inventory_disputes"
    __table_args__ = MYSQL_TABLE_OPTIONS

    review_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default=str_default("open")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InventoryDevelopmentPlan(PKMixin, CTimestampMixin, Base):
    """个人发展计划（IDP）。"""

    __tablename__ = "inventory_development_plans"
    __table_args__ = (
        UniqueConstraint("review_id", name="uk_idp_review"),
        MYSQL_TABLE_OPTIONS,
    )

    review_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=str_default("draft")
    )


class InventoryEvent(PKMixin, CTimestampMixin, Base):
    """盘点操作审计。"""

    __tablename__ = "inventory_events"
    __table_args__ = MYSQL_TABLE_OPTIONS

    review_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONDict, nullable=False, default=dict)
