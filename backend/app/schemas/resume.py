"""简历上传 DTO（T-10，`docs/接口设计.md` v1.3 §4）。

字段名与接口文档逐字一致（`upload_batch_id` / `file_count` / `can_requeue` ...），
前端可以直接按此定义 TS 类型，不需要再猜。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RejectedItem(BaseModel):
    """被拒文件（类型/大小/内容不符），逐项回显原因。"""

    filename: str = Field(description="原始文件名（已剥离路径，仅最后一段）")
    reason: str = Field(description="拒绝原因，中文")


class DuplicateItem(BaseModel):
    """命中去重的文件：**不报错**，跳过并回显原任务，便于前端给"重新入队"入口。"""

    filename: str
    reason: str = Field(description="固定为「文件内容已存在」")
    resume_file_id: int
    task_id: int | None = Field(default=None, description="原解析任务 id；缺失时为 null")
    task_status: str | None = Field(default=None, description="pending/running/success/failed/duplicate_pending/cancelled")
    can_requeue: bool = Field(description="仅 cancelled / failed 为 true，其余 false")


class BatchUploadData(BaseModel):
    """`POST /resumes/batch` 的 data（HTTP 202）。"""

    upload_batch_id: str = Field(description="本次上传的 ULID（26 字符）")
    accepted: int = Field(description="新入队文件数")
    duplicates: list[DuplicateItem] = Field(default_factory=list)
    rejected: list[RejectedItem] = Field(default_factory=list)
    queue_total: int = Field(description="当前 pending 任务总数（含本次）")


class BatchSummaryItem(BaseModel):
    """`GET /resumes/batches` 的 items 项。"""

    model_config = ConfigDict(from_attributes=True)

    upload_batch_id: str
    file_count: int
    success: int
    failed: int
    pending: int = Field(description="尚未出结果的数量（含 running/duplicate_pending/cancelled）")
    uploaded_at: datetime


# ---------------------------------------------------------------- T-14 队列管理（接口 §4）


class QueueSummary(BaseModel):
    """六个状态计数 + total（`GET /resumes/queue` 的 `summary`）。"""

    pending: int
    running: int
    success: int
    failed: int
    duplicate_pending: int
    cancelled: int
    total: int


class QueueItem(BaseModel):
    """队列列表项；字段名与接口 §4 逐字一致，前端直接映射表格。"""

    task_id: int
    resume_file_id: int
    filename: str
    upload_batch_id: str
    status: str = Field(description="旧字段：pending/running/success/failed/duplicate_pending/cancelled")
    task_status: str = Field(description="对外契约：waiting/running/completed/failed/cancelled")
    duplicate_pending: bool = Field(
        default=False,
        description="为 true 时 task_status=completed，但仍需人工处理重复",
    )
    stage: str | None = Field(default=None, description="extract/profile/vector；pending 时可为 null")
    retries: int
    error: str | None = None
    truncated: bool = Field(description="文本是否被截断")
    created_at: datetime = Field(description="取 resume_files.created_at（上传时间）")
    started_at: datetime | None = None
    finished_at: datetime | None = None
    profiled_at: datetime | None = None
    talent_profile_id: int | None = None
    can_retry: bool = Field(description="仅 failed 为 true")
    can_requeue: bool = Field(description="cancelled / failed 为 true")


class QueueListData(BaseModel):
    """`GET /resumes/queue` 的 data；**不是通用分页体**（多 summary/concurrency 两个键）。"""

    summary: QueueSummary
    concurrency: int = Field(description="sys_settings.task_concurrency 当前值")
    items: list[QueueItem]
    total: int
    page: int
    page_size: int


class QueueSettingsRequest(BaseModel):
    """`PATCH /resumes/queue/settings` 请求体。

    **不在 Pydantic 层加 ge/le**：越界要求返回 400（接口 §4），而 Pydantic 抛 422；
    值域在 `queue_service` 校验，结构非法（非整数）才走 422。
    """

    concurrency: int = Field(description="TaskRunner 全局并发，1–5；越界返回 400")


class QueueSettingsData(BaseModel):
    concurrency: int
    effective: str = Field(default="next_scan", description="下一个扫描周期（2s）生效")


class TaskActionData(BaseModel):
    """retry / requeue / delete 的返回：动作后的任务快照（够前端就地刷新一行）。"""

    task_id: int
    status: str
    task_status: str = Field(description="对外契约：waiting/running/completed/failed/cancelled")
    stage: str | None
    retries: int
