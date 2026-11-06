"""C 培训域请求 DTO（契约 §4）。"""

from __future__ import annotations

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


class CourseUpdateRequest(_StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None
    material_url: str | None = Field(default=None, min_length=1, max_length=500)
    dimension_names: list[str] | None = Field(default=None, max_length=20)
    status: CourseStatus | None = None


class PlanItemIn(_StrictModel):
    course_id: int
    dimension_name: str | None = Field(default=None, max_length=100)
    reason: str | None = None


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
