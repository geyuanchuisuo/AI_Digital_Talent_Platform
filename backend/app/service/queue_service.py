"""解析队列管理（T-14，需求 F1-2 / 接口 §4 / 数据库 §4.2.1）。

五个动作的语义边界（不要混）：

- `GET /resumes/queue`：只读，summary 为六状态计数（按 upload_batch_id 收窄），
  `status` 筛选只作用于列表，不影响 summary（状态 tab 角标要全量数）。
- `PATCH /resumes/queue/settings`：写 `sys_settings.task_concurrency`（决策 D-C）。
  这是 **TaskRunner 全局并发**（解析/筛选/匹配共用池），不是"仅解析并发"；
  runner 每 2s 读一次，在无在跑任务时重建线程池 → `effective: "next_scan"`。
- `POST .../retry`：仅 `failed`，**保留 `stage`** 断点续跑（决策 D-I）。
- `POST .../requeue`：`cancelled`/`failed` 可入队，**`stage=NULL` 从头跑**（决策 D-H）；
  与 `resume_files` 恒 1:1 且不删行，所以**不插新行**，不会撞 `uk_resume_file`。
- `DELETE ...`：**软取消**置 `cancelled`，文件与已产出数据都不删；`running` 拒绝 400，
  已 `cancelled` 再删 404（活动队列中已不存在）。

事务边界：DAO 只 flush，本文件 commit。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BizError
from app.dao import parse_tasks as parse_task_dao
from app.dao import sys_settings as sys_setting_dao
from app.models import ParseTask, ResumeFile
from app.schemas.resume import QueueItem, QueueListData, QueueSettingsData, QueueSummary, TaskActionData

logger = logging.getLogger("app.queue")

CONCURRENCY_KEY = "task_concurrency"
CONCURRENCY_DESCRIPTION = "TaskRunner 全局任务并发（1–5，解析/筛选/匹配共用）"
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 5

RETRYABLE_STATUSES = parse_task_dao.RETRYABLE_STATUSES
REQUEUEABLE_STATUSES = parse_task_dao.REQUEUEABLE_STATUSES
#: DELETE 队列任务允许的当前状态；running 拒绝（接口 §4）
CANCELLABLE_STATUSES = frozenset({"pending", "failed", "success", "duplicate_pending"})

_STATUS_TEXT = {
    "pending": "等待中",
    "running": "执行中",
    "success": "已完成",
    "failed": "失败",
    "duplicate_pending": "待查重确认",
    "cancelled": "已取消",
}


def validate_concurrency(value: int) -> int:
    """1–5 整数校验；越界抛 400（接口 §4 要求 400，不能用 Pydantic 的 422）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise BizError(400, f"并发数必须是 {MIN_CONCURRENCY}–{MAX_CONCURRENCY} 的整数")
    if value < MIN_CONCURRENCY or value > MAX_CONCURRENCY:
        raise BizError(400, f"并发数必须在 {MIN_CONCURRENCY}–{MAX_CONCURRENCY} 之间")
    return value


def build_item(task: ParseTask, resume_file: ResumeFile, talent_profile_id: int | None) -> dict:
    """把 ORM 行摊平成接口 §4 的 item（纯函数，便于单测按钮显隐规则）。"""
    return {
        "task_id": task.id,
        "resume_file_id": task.resume_file_id,
        "filename": resume_file.filename,
        "upload_batch_id": resume_file.upload_batch_id,
        "status": task.status,
        "stage": task.stage,
        "retries": task.retries,
        "error": task.error,
        "truncated": bool(task.truncated),
        "created_at": resume_file.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "profiled_at": task.profiled_at,
        "talent_profile_id": talent_profile_id,
        "can_retry": task.status in RETRYABLE_STATUSES,
        "can_requeue": task.status in REQUEUEABLE_STATUSES,
    }


def list_queue(
    db: Session,
    *,
    status: str | None,
    upload_batch_id: str | None,
    page: int,
    page_size: int,
    offset: int,
) -> dict:
    """`GET /resumes/queue` 的 data（接口 §4）。"""
    if status and status not in parse_task_dao.QUEUE_STATUSES:
        allowed = "/".join(parse_task_dao.QUEUE_STATUSES)
        raise BizError(400, f"status 只能是 {allowed}")

    rows = parse_task_dao.list_queue(
        db, status=status, upload_batch_id=upload_batch_id, offset=offset, limit=page_size
    )
    total = parse_task_dao.count_queue(db, status=status, upload_batch_id=upload_batch_id)
    summary = parse_task_dao.status_summary(db, upload_batch_id=upload_batch_id)
    concurrency = sys_setting_dao.get_int(db, CONCURRENCY_KEY, int(settings.task_concurrency))
    return QueueListData(
        summary=QueueSummary(**summary),
        concurrency=concurrency,
        items=[QueueItem(**build_item(task, resume_file, talent_id)) for task, resume_file, talent_id in rows],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()


def update_concurrency(db: Session, concurrency: int, *, updated_by: int) -> dict:
    """`PATCH /resumes/queue/settings`：持久化到 `sys_settings`，下个扫描周期生效。"""
    value = validate_concurrency(concurrency)
    sys_setting_dao.set_value(
        db,
        CONCURRENCY_KEY,
        str(value),
        value_type="int",
        description=CONCURRENCY_DESCRIPTION,
        updated_by=updated_by,
    )
    db.commit()
    # 当前进程的运行时值同步一份；runner 每周期也会 load_runtime_overrides() 覆盖
    settings.task_concurrency = value
    logger.info("queue: task_concurrency=%d by user=%s（next_scan 生效）", value, updated_by)
    return QueueSettingsData(concurrency=value, effective="next_scan").model_dump()


def _get_task(db: Session, task_id: int) -> ParseTask:
    task = parse_task_dao.get_by_id(db, task_id)
    if task is None:
        raise BizError(404, "任务不存在")
    return task


def _action_payload(task: ParseTask) -> dict:
    return TaskActionData(
        task_id=task.id, status=task.status, stage=task.stage, retries=task.retries
    ).model_dump()


def retry_task(db: Session, task_id: int) -> dict:
    """仅 `failed`；**保留 `stage`**，worker 从断点续跑（接口 §4）。"""
    task = _get_task(db, task_id)
    if task.status not in RETRYABLE_STATUSES:
        raise BizError(
            400, f"仅失败任务可重试，当前状态为「{_STATUS_TEXT.get(task.status, task.status)}」"
        )
    task.status = "pending"
    task.error = None
    task.retries += 1
    task.started_at = None
    task.heartbeat_at = None
    task.finished_at = None
    # stage 保留：vector 只补向量、profile 从查重/画像续跑（数据库 §4.2.1）
    db.commit()
    db.refresh(task)
    logger.info("queue: retry task=%s stage=%s retries=%d", task.id, task.stage, task.retries)
    return _action_payload(task)


def requeue_task(db: Session, task_id: int) -> dict:
    """`cancelled`/`failed` → pending，**stage=NULL 全流程重跑**（决策 D-H）。"""
    task = _get_task(db, task_id)
    if task.status not in REQUEUEABLE_STATUSES:
        raise BizError(
            400, f"仅已取消或失败任务可重新入队，当前状态为「{_STATUS_TEXT.get(task.status, task.status)}」"
        )
    task.status = "pending"
    task.stage = None
    task.error = None
    task.retries += 1
    task.started_at = None
    task.heartbeat_at = None
    task.finished_at = None
    # 与 retry 的唯一语义差异就是 stage：这里清空，worker 走全流程（接口 §4）
    # `profiled_at` 不动：已有画像由 T-16 幂等复用，前端据 talent_profile_id/profiled_at 展示更一致
    db.commit()
    db.refresh(task)
    logger.info("queue: requeue task=%s retries=%d（stage 已清空）", task.id, task.retries)
    return _action_payload(task)


def cancel_task(db: Session, task_id: int) -> dict:
    """软取消（接口 §4）：running 400；已 cancelled 视为不存在 → 404。"""
    task = _get_task(db, task_id)
    if task.status == "cancelled":
        raise BizError(404, "任务不存在")
    if task.status not in CANCELLABLE_STATUSES:
        raise BizError(400, "任务正在执行，不能取消；请等待完成或失败后再操作")
    task.status = "cancelled"
    db.commit()
    db.refresh(task)
    logger.info("queue: cancel task=%s（软取消，文件与画像保留）", task.id)
    return _action_payload(task)
