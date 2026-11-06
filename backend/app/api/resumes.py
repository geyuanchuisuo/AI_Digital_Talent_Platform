"""简历上传、批次汇总与解析队列（T-10 / T-14）。

- 上传与批次：`POST /api/resumes/batch`、`GET /api/resumes/batches`（T-10）。
- 队列管理：`GET /api/resumes/queue`、`PATCH /queue/settings`、
  `POST /queue/{task_id}/retry|requeue`、`DELETE /queue/{task_id}`（T-14）。

口径：`docs/接口设计.md` v1.3 §4、需求 F1-1/F1-2。
Controller 职责边界（架构 §5.2）：**只接收请求参数 + 返回统一体**，
扩展名校验、magic bytes、SHA-256 去重、落盘、写 `resume_files`/`parse_tasks`
全部在 `app.service.upload_service`；队列状态流转与并发写入在
`app.service.queue_service`，此处不碰文件系统、不直接查 MySQL。

两条不要动的口径：
- **没有 `upload_batches` 表**（决策 D-M）：`/batches` 是对 `resume_files.upload_batch_id`
  的实时聚合，不是查批次实体表。
- **`UploadFile.filename` 原样透传**（H-13）：不做 latin-1→GBK 猜测转码，命令行 curl
  乱码是 Git Bash 的编码问题，浏览器/TestClient 均为 UTF-8。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination
from app.core.response import ok, paginated
from app.database import get_db
from app.schemas.resume import BatchSummaryItem, QueueSettingsRequest
from app.service import queue_service, upload_service

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


@router.post("/batch", summary="批量上传简历（PDF/DOCX）")
def upload_batch(
    files: Annotated[list[UploadFile], File(description="多份简历，单文件 ≤20MB，支持 pdf/docx")],
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """成功 202（不是 200）；全部命中重复也是 202（`accepted=0`）。

    只有"一个有效文件都没有"（全非法/超限）才 400，原因逐项列在 `message` 里
    （统一错误体没有 data 字段，接口 §1.1）。
    """
    data = upload_service.save_batch(db, files, user.id)
    return ok(data, status_code=202)


@router.get("/batches", summary="上传批次汇总（供筛选范围下拉）")
def list_batches(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
):
    """按 `upload_batch_id` 聚合，`uploaded_at` 倒序；`total` 为批次数。"""
    items, total = upload_service.list_batches(db, offset=page.offset, limit=page.limit)
    return paginated(
        [BatchSummaryItem.model_validate(item).model_dump() for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/queue", summary="解析队列（分页 + 状态/批次筛选）")
def list_queue(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    status: Annotated[
        str | None,
        Query(description="pending/running/success/failed/duplicate_pending/cancelled"),
    ] = None,
    upload_batch_id: Annotated[str | None, Query(description="上传批次 ULID，可按批次视角查看")] = None,
):
    """data 比通用分页体多 `summary` 与 `concurrency` 两个键（接口 §4）。

    **不用 `paginated()`**：那会把 data 拍平成 `{items,total,page,page_size}`，
    丢掉 summary/concurrency。
    """
    data = queue_service.list_queue(
        db,
        status=status,
        upload_batch_id=upload_batch_id,
        page=page.page,
        page_size=page.page_size,
        offset=page.offset,
    )
    return ok(data)


@router.patch("/queue/settings", summary="调整 TaskRunner 全局并发（1–5）")
def update_queue_settings(
    payload: QueueSettingsRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """写 `sys_settings.task_concurrency`（持久化）；下个扫描周期生效。"""
    data = queue_service.update_concurrency(db, payload.concurrency, updated_by=user.id)
    return ok(data)


@router.post("/queue/{task_id}/retry", summary="重试失败任务（断点续跑，保留 stage）")
def retry_task(task_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    """仅 `failed`；`stage` 保留，`vector` 阶段重试不再调 LLM。"""
    return ok(queue_service.retry_task(db, task_id))


@router.post("/queue/{task_id}/requeue", summary="重新入队（cancelled/failed，从头重跑）")
def requeue_task(task_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    """`stage` 置 NULL；**不新插 parse_tasks 行**（与 resume_files 恒 1:1）。"""
    return ok(queue_service.requeue_task(db, task_id))


@router.delete("/queue/{task_id}", summary="软取消队列任务（保留文件与已产出数据）")
def cancel_task(task_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    """`running` 拒绝 400；已 `cancelled` 再删 404；恢复入口是 requeue。"""
    return ok(queue_service.cancel_task(db, task_id))
