"""匹配/筛选 LLM 评分契约（T-23，`docs/LLM提示词设计.md` v1.2 Prompt C、算法 §4.3）。

维度键名固定四个：skills / experience / education / others（+ reason）。
`hard_fail / hard_fail_reason` 不在 LLM 输出里——规则层在调用前完成硬门槛判定，
硬淘汰者根本不进入本 prompt（Prompt C 系统提示明示）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DIM_KEYS: tuple[str, ...] = ("skills", "experience", "education", "others")
REASON_MAX_CHARS = 200


class ScoreDimension(BaseModel):
    """单维输出：分数 + 命中/缺失项（LLM 视角；硬门槛命中项由规则层另给）。"""

    model_config = ConfigDict(extra="forbid")

    score: int
    hits: list[str] = Field(default_factory=list)
    misses: list[str] = Field(default_factory=list)


class MatchScoreOutput(BaseModel):
    """Prompt C 的完整输出契约（chat_json 的 Pydantic 校验目标）。"""

    model_config = ConfigDict(extra="forbid")

    skills: ScoreDimension
    experience: ScoreDimension
    education: ScoreDimension
    others: ScoreDimension
    reason: str = Field(default="", max_length=REASON_MAX_CHARS)


# =========================================================================== #
# T-25 匹配批次 DTO（接口 §8）
# =========================================================================== #
class MatchScopePayload(BaseModel):
    """scope 三种（附录 B）；filter 的 degree/min_years/tags 均可选（AND 语义）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["all", "filter", "upload_batch"]
    upload_batch_id: str | None = Field(default=None, max_length=26)
    degree: str | None = Field(default=None, max_length=20)
    min_years: float | None = Field(default=None, ge=0, le=80)
    tags: list[str] | None = None


class MatchBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_profile_id: int = Field(ge=1)
    scope: MatchScopePayload


class MatchBatchCreatedData(BaseModel):
    batch_id: int
    total: int
    status: str
    estimated_llm_calls: int


class MatchBatchListItem(BaseModel):
    batch_id: int
    job_profile_id: int
    job_title: str | None = None
    job_profile_version: int
    status: str
    progress: int
    total: int
    hard_failed: int
    retry_count: int
    vector_degraded: bool
    created_at: datetime
    finished_at: datetime | None = None


class MatchBatchDetail(BaseModel):
    batch_id: int
    job_profile_id: int
    job_profile_version: int
    config_snapshot: dict[str, Any]
    scope: dict[str, Any]
    status: str
    progress: int
    total: int
    hard_failed: int
    processed: int
    skipped_deleted: int
    vector_degraded: bool = Field(description="true = 本批次向量依赖不可用，经历维度仅按 LLM 评分")
    error: str | None = None
    retry_count: int
    created_at: datetime
    finished_at: datetime | None = None


class MatchRetryData(BaseModel):
    batch_id: int
    status: str
    retry_count: int
    total: int
    progress: int


class MatchResultItem(BaseModel):
    """`GET /match/batches/{id}/results` 的 items（rank 查询时计算）。"""

    result_id: int
    rank: int
    talent_profile_id: int
    name: str
    deleted: bool = False
    current_title: str | None = None
    score: float = Field(description="硬淘汰 score=0（置底排序）")
    hard_fail: bool
    hard_fail_reason: str | None = None
    hits_count: int
    misses_count: int


class MatchResultDetail(BaseModel):
    """`GET /match/results/{id}`（F3-4/F3-5 理由详情）。"""

    result_id: int
    rank: int
    talent_profile_id: int
    name: str
    deleted: bool = False
    current_title: str | None = None
    score: float
    hard_fail: bool
    hard_fail_reason: str | None = None
    dimension_scores: dict[str, Any] = Field(description="四维分；vector_similarity 键可能为 null（降级）")
    vector_similarity: float | None = None
    vector_degraded: bool = Field(description="本批次向量依赖不可用（经历维度仅按 LLM 评分）")
    hits: list[str] = Field(default_factory=list)
    misses: list[str] = Field(default_factory=list)
    reason: str | None = None
    # 人工推进标记（T-A43/T-A41：对齐 screen_results.manual_verdict*；null=未改判）
    manual_verdict: Literal["passed", "pending", "rejected"] | None = None
    manual_verdict_by: int | None = None
    manual_verdict_at: str | None = None


class ManualVerdictRequest(BaseModel):
    """人工改判请求（F1-10 对齐 screen；null = 撤销改判）。"""

    model_config = ConfigDict(extra="forbid")

    manual_verdict: Literal["passed", "pending", "rejected"] | None = Field(
        description="null = 撤销改判"
    )
