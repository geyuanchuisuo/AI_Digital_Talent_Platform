"""F10 人才盘点接口 schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReviewStatus = Literal["draft", "submitted", "confirmed", "cancelled"]
ReviewType = Literal["annual", "ad_hoc"]
PoolStatus = Literal["nominated", "approved", "rejected", "removed"]
DisputeStatus = Literal["open", "resolved", "rejected"]


class ReviewCreate(BaseModel):
    employee_user_id: int
    talent_profile_id: int | None = None
    cycle_name: str = Field(min_length=1, max_length=50)
    review_type: ReviewType = "annual"
    performance_score: int | None = Field(default=None, ge=1, le=5)
    performance_source: str = "none"
    potential_manager_score: int | None = Field(default=None, ge=1, le=5)
    potential_hr_score: int | None = Field(default=None, ge=1, le=5)
    reviewer_manager_id: int | None = None
    reviewer_hr_id: int | None = None
    summary: str | None = None


class ReviewUpdate(BaseModel):
    performance_score: int | None = Field(default=None, ge=1, le=5)
    performance_source: str | None = None
    potential_manager_score: int | None = Field(default=None, ge=1, le=5)
    potential_hr_score: int | None = Field(default=None, ge=1, le=5)
    reviewer_manager_id: int | None = None
    reviewer_hr_id: int | None = None
    summary: str | None = None


class PoolNominate(BaseModel):
    employee_user_id: int
    pool_type: str = Field(min_length=1, max_length=30)
    note: str | None = None


class PoolDecide(BaseModel):
    status: Literal["approved", "rejected", "removed"]
    note: str | None = None


class DisputeCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class DisputeRespond(BaseModel):
    status: Literal["resolved", "rejected"]
    response: str = Field(min_length=1, max_length=2000)


class IdpUpsert(BaseModel):
    content_json: dict[str, Any]
    status: Literal["draft", "confirmed"] = "draft"
