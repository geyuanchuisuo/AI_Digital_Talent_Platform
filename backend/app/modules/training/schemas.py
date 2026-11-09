"""C 培训域请求 DTO（契约 §4）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CourseStatus = Literal["active", "inactive"]
LearningStatus = Literal["not_started", "learning", "completed"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CourseCreateRequest(_StrictModel):
    title: str = Field(min_length=1, max_length=150)
    description: str | None = None
    material_url: str = Field(min_length=1, max_length=500)
    dimension_names: list[str] = Field(default_factory=list, max_length=20)
    status: CourseStatus = "active"
    capacity: int | None = Field(default=None, ge=1, le=10000)


class CourseUpdateRequest(_StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None
    material_url: str | None = Field(default=None, min_length=1, max_length=500)
    dimension_names: list[str] | None = Field(default=None, max_length=20)
    status: CourseStatus | None = None
    capacity: int | None = Field(default=None, ge=1, le=10000)


class PlanItemIn(_StrictModel):
    course_id: int
    dimension_name: str | None = Field(default=None, max_length=100)
    reason: str | None = None
    due_date: date | None = None


class DraftRequest(_StrictModel):
    employee_user_id: int
    talent_profile_id: int | None = None
    job_id: int | None = None
    target_job_title: str | None = Field(default=None, max_length=100)
    dimension_gaps: list[str] = Field(default_factory=list, max_length=20)
    assessment_assignment_id: int | None = None
    profile_summary: str | None = None


class PlanCreateRequest(_StrictModel):
    employee_user_id: int
    talent_profile_id: int | None = None
    job_id: int | None = None
    target_job_title: str | None = Field(default=None, max_length=100)
    items: list[PlanItemIn] = Field(min_length=1, max_length=50)
    gap_summary: str | None = None


class PlanUpdateRequest(_StrictModel):
    target_job_title: str | None = Field(default=None, max_length=100)
    items: list[PlanItemIn] | None = Field(default=None, min_length=1, max_length=50)
    gap_summary: str | None = None


class ProgressUpdateRequest(_StrictModel):
    status: LearningStatus
    note: str | None = Field(default=None, max_length=500)


class SessionCreateRequest(_StrictModel):
    instructor_name: str | None = Field(default=None, max_length=100)
    starts_at: datetime
    ends_at: datetime


class AttendanceMarkRequest(_StrictModel):
    employee_user_id: int


class FeedbackSubmitRequest(_StrictModel):
    content_score: int = Field(ge=1, le=5)
    instructor_score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackNoteRequest(_StrictModel):
    note: str = Field(min_length=1, max_length=2000)


class EnrollmentRequest(_StrictModel):
    course_id: int
    priority: Literal["required", "elective"] = "elective"


class EnrollmentDecideRequest(_StrictModel):
    status: Literal["approved", "rejected", "cancelled"]
    note: str | None = Field(default=None, max_length=500)


class EvidenceSubmitRequest(_StrictModel):
    learning_record_id: int
    evidence_type: str = Field(min_length=1, max_length=30)
    payload: dict = Field(default_factory=dict)
    score: float = Field(default=0.0, ge=0, le=100)
    verified_by: str | None = Field(default=None, max_length=30)


class ExtensionDecideRequest(_StrictModel):
    """管理员延期必修课期限（F09：必须填写原因）。"""

    new_due_date: date
    reason: str = Field(min_length=1, max_length=500)
    manager_user_id: int | None = None


class ReminderScanRequest(_StrictModel):
    """到期提醒/逾期扫描入口；today 缺省取服务器当天。"""

    today: date | None = None
    days_ahead: int = Field(default=3, ge=0, le=30)
