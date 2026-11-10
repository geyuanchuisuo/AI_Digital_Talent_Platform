"""筛选批次 DTO（T-24，接口 §7）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScopePayload(BaseModel):
    """scope 四种（附录 B）；字段随 type 校验，业务校验在 service（统一 400）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["all_unscreened", "upload_batch", "resume_file_ids", "all"]
    upload_batch_id: str | None = Field(default=None, max_length=26)
    ids: list[int] | None = None


class ScreenBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_profile_id: int = Field(ge=1)
    scope: ScopePayload
    # T-A42：可选请求级幂等键；空白＝未提供，超长由 service 统一返回 400。
    idempotency_key: str | None = Field(
        default=None, description="同一岗位下重复提交返回原批次，不新建结果"
    )


class ScreenBatchCreatedData(BaseModel):
    """POST /screen/batches 的 202 data。"""

    batch_id: int
    total: int
    status: str
    estimated_llm_calls: int


class ScreenBatchListItem(BaseModel):
    """`GET /screen/batches` 的 items 项（接口 §7 字段集合）。"""

    batch_id: int
    job_profile_id: int
    job_title: str | None = None
    job_profile_version: int
    status: str
    progress: int
    total: int
    passed: int
    pending_count: int
    rejected: int
    retry_count: int
    created_at: datetime
    finished_at: datetime | None = None


class ScreenBatchDetail(BaseModel):
    """`GET /screen/batches/{id}`（轮询）：详情 + scope 摘要 + processed/skipped_deleted。"""

    batch_id: int
    job_profile_id: int
    job_profile_version: int
    config_snapshot: dict[str, Any]
    scope: dict[str, Any] = Field(description="原始条件 + resolved_ids_count（不含全量清单）")
    status: str
    progress: int
    total: int
    passed: int
    pending_count: int
    rejected: int
    processed: int
    skipped_deleted: int
    vector_degraded: bool = Field(description="筛选批次恒为 false（算法 §4.0）")
    error: str | None = None
    retry_count: int
    created_at: datetime
    finished_at: datetime | None = None


class ScreenRetryData(BaseModel):
    batch_id: int
    status: str
    retry_count: int
    total: int
    progress: int


class ScreenResultItem(BaseModel):
    """`GET /screen/batches/{id}/results` 的 items 项（接口 §7）。"""

    result_id: int
    talent_profile_id: int
    name: str
    deleted: bool = Field(default=False, description="画像已软删/不存在（历史行保留，姓名兜底）")
    current_title: str | None = None
    degree: str | None = None
    work_years: float | None = None
    hard_fail: bool
    hard_fail_reason: str | None = None
    score: float | None = Field(default=None, description="硬淘汰为 null（筛选口径）")
    verdict: str
    manual_verdict: str | None = None
    effective_verdict: str = Field(description="manual_verdict ?? verdict")
    manual_verdict_by: int | None = None
    manual_verdict_at: datetime | None = None
    reason_summary: str | None = None
    hits: list[str] = Field(default_factory=list)
    misses: list[str] = Field(default_factory=list)
    created_at: datetime


class ManualVerdictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_verdict: Literal["passed", "pending", "rejected"] | None = Field(
        description="null = 撤销改判"
    )
