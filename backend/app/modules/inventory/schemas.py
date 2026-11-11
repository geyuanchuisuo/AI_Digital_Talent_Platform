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
    summary: str | None = None


class ReviewUpdate(BaseModel):
    performance_score: int | None = Field(default=None, ge=1, le=5)
    performance_source: str | None = None
    potential_hr_score: int | None = Field(default=None, ge=1, le=5)
    summary: str | None = None


class ManagerInitialReview(BaseModel):
    """经理本人潜力初评：初评人由服务端记为调用经理，不接受客户端指定。"""

    potential_manager_score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


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


class GridConfigCreate(BaseModel):
    """九宫格标准配置草稿：阈值须显式给出（1–5），不在代码里套示意数值。"""

    job_family: str | None = Field(default=None, max_length=100)
    performance_bands: dict[str, int]
    potential_bands: dict[str, int]
    labels: dict[str, Any] | None = None
    note: str | None = None

