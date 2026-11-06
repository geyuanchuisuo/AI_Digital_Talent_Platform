"""C 人工面试域请求 DTO（契约 §4）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RoundMode = Literal["onsite", "online", "phone"]
RoundStatus = Literal["pending", "completed"]
Recommendation = Literal["advance", "hold", "reject"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoundCreateRequest(_StrictModel):
    candidate_user_id: int | None = None
    talent_profile_id: int | None = None
    job_id: int | None = None
    job_title: str | None = Field(default=None, max_length=100)
    round_no: int = Field(default=1, ge=1, le=20)
    round_name: str = Field(min_length=1, max_length=50)
    scheduled_at: datetime | None = None
    mode: RoundMode = "onsite"
    location_or_link: str | None = Field(default=None, max_length=500)
    interviewer_user_ids: list[int] = Field(min_length=1, max_length=20)
    internal_note: str | None = None


class RoundUpdateRequest(_StrictModel):
    round_name: str | None = Field(default=None, min_length=1, max_length=50)
    scheduled_at: datetime | None = None
    mode: RoundMode | None = None
    location_or_link: str | None = Field(default=None, max_length=500)
    interviewer_user_ids: list[int] | None = Field(default=None, min_length=1, max_length=20)
    status: RoundStatus | None = None
    internal_note: str | None = None


class FeedbackSaveRequest(_StrictModel):
    evaluation: str = Field(min_length=1)
    evidence: str | None = None
    strengths: str | None = None
    weaknesses: str | None = None
    recommendation: Recommendation = "hold"
