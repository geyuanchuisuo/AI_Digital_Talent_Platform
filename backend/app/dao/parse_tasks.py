"""`parse_tasks` 数据访问（T-10 建行 / T-14 队列管理）。

T-10 用到"建 pending 任务""按文件查任务""数 pending 存量"，供上传接口回显
`duplicates[].task_status/task_id` 与 `queue_total`。T-14 追加队列列表、
状态汇总、单任务查询；认领与心跳流转在 `tasks/runner.py`（T-12），不在本文件。

队列列表/汇总需要 `resume_files`（文件名、批次、创建时间）与 `parsed_resumes`
（`talent_profile_id` 展示），因此这里做只读 join；**不写裸 SQL**。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import ParseTask, ParsedResume, ResumeFile

#: 解析队列的全部状态（接口 §4 `summary` 六个计数键）
QUEUE_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "success",
    "failed",
    "duplicate_pending",
    "cancelled",
)

#: 上传接口里 `can_requeue` 为 true 的状态（接口 §4：仅 cancelled / failed）
REQUEUEABLE_STATUSES = frozenset({"cancelled", "failed"})

#: `POST /resumes/queue/{id}/retry` 只接受 failed（接口 §4）
RETRYABLE_STATUSES = frozenset({"failed"})


def create_pending(db: Session, resume_file_id: int) -> ParseTask:
    """与 `resume_files` 1:1 的 pending 任务；`stage` 留空表示从头跑。"""
    row = ParseTask(resume_file_id=resume_file_id, status="pending", stage=None, retries=0, truncated=0)
    db.add(row)
    db.flush()
    return row


def get_by_resume_file(db: Session, resume_file_id: int) -> ParseTask | None:
    return db.execute(
        select(ParseTask).where(ParseTask.resume_file_id == resume_file_id)
    ).scalar_one_or_none()


def map_by_resume_file_ids(db: Session, resume_file_ids: Iterable[int]) -> dict[int, ParseTask]:
    """批量取任务，避免 duplicates 循环里逐条查库（一次上传可能几十份）。"""
    ids = list(resume_file_ids)
    if not ids:
        return {}
    rows = db.execute(select(ParseTask).where(ParseTask.resume_file_id.in_(ids))).scalars().all()
    return {row.resume_file_id: row for row in rows}


def count_pending(db: Session) -> int:
    """当前待解析存量（`queue_total` 口径：pending 状态任务数）。"""
    return int(
        db.execute(select(func.count(ParseTask.id)).where(ParseTask.status == "pending")).scalar_one()
    )


def delete_by_resume_file_ids(db: Session, resume_file_ids: Iterable[int]) -> int:
    """物理删除任务行（**仅供测试/运维清理**；业务上"删任务"是置 cancelled）。"""
    ids = list(resume_file_ids)
    if not ids:
        return 0
    rows = db.execute(select(ParseTask).where(ParseTask.resume_file_id.in_(ids))).scalars().all()
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


# ---------------------------------------------------------------- T-14 队列管理


def get_by_id(db: Session, task_id: int) -> ParseTask | None:
    """按主键取任务（retry/requeue/delete 的入口）。"""
    return db.get(ParseTask, task_id)


def _queue_stmt() -> Select:
    """队列基础查询：任务 + 文件 + 解析结果，供列表与计数复用。

    `parse_tasks` 没有 `created_at`（只有 `updated_at`），接口里的 `created_at`
    取 `resume_files.created_at`（上传时间，也是队列的天然排序键）。
    `parsed_resumes` 用外连接：任务可能还没产出解析结果（pending/failed）。
    """
    return (
        select(
            ParseTask,
            ResumeFile,
            ParsedResume.talent_profile_id.label("talent_profile_id"),
        )
        .join(ResumeFile, ResumeFile.id == ParseTask.resume_file_id)
        .outerjoin(ParsedResume, ParsedResume.resume_file_id == ParseTask.resume_file_id)
    )


def list_queue(
    db: Session,
    *,
    status: str | None,
    upload_batch_id: str | None,
    offset: int,
    limit: int,
) -> list[tuple[ParseTask, ResumeFile, int | None]]:
    """队列分页（接口 §4）：按文件上传时间倒序，最近上传的排在最前。"""
    stmt = _queue_stmt()
    if status:
        stmt = stmt.where(ParseTask.status == status)
    if upload_batch_id:
        stmt = stmt.where(ResumeFile.upload_batch_id == upload_batch_id)
    stmt = stmt.order_by(ResumeFile.created_at.desc(), ParseTask.id.desc()).offset(offset).limit(limit)
    return [(task, resume_file, talent_id) for task, resume_file, talent_id in db.execute(stmt).all()]


def count_queue(db: Session, *, status: str | None, upload_batch_id: str | None) -> int:
    """当前筛选条件下的任务数（分页 `total`）。"""
    stmt = select(func.count(ParseTask.id)).join(ResumeFile, ResumeFile.id == ParseTask.resume_file_id)
    if status:
        stmt = stmt.where(ParseTask.status == status)
    if upload_batch_id:
        stmt = stmt.where(ResumeFile.upload_batch_id == upload_batch_id)
    return int(db.execute(stmt).scalar_one())


def status_summary(db: Session, *, upload_batch_id: str | None = None) -> dict[str, int]:
    """六个状态计数 + `total`（接口 §4 `summary`）。

    `upload_batch_id` 用于批次视角卡片的汇总；`status` 筛选只影响列表，
    不影响 summary（前端状态 tab 的角标需要全量计数）。
    """
    stmt = select(ParseTask.status, func.count(ParseTask.id)).join(
        ResumeFile, ResumeFile.id == ParseTask.resume_file_id
    )
    if upload_batch_id:
        stmt = stmt.where(ResumeFile.upload_batch_id == upload_batch_id)
    counts = {status: 0 for status in QUEUE_STATUSES}
    for status, count in db.execute(stmt.group_by(ParseTask.status)).all():
        counts[status] = int(count)
    counts["total"] = sum(counts[status] for status in QUEUE_STATUSES)
    return counts
