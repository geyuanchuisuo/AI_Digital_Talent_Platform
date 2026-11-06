"""`resume_files` 数据访问（T-10）。

约定（与 `dao/users.py` 一致）：
- **不 commit**：事务边界由 service 决定，DAO 只做 `add` / `flush` / 查询。
- 不在 DAO 里做业务判断（去重与否、能不能重传），只提供事实查询。

⚠️ **批次不是实体表**（决策 D-M）：`upload_batch_id` 是 `resume_files` 上的普通字段，
   批次列表/计数都在这里用 `GROUP BY` / `COUNT(DISTINCT ...)` 现算。别新增
   `upload_batches` 表再来 join——一期 13 张表冻结，且聚合结果与实时状态天然一致。
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import ParseTask, ParsedResume, ResumeFile


def get_by_hash(db: Session, file_hash: str) -> ResumeFile | None:
    """按内容 SHA-256 找已有文件行（`uk_file_hash` 唯一，最多一行）。"""
    return db.execute(select(ResumeFile).where(ResumeFile.file_hash == file_hash)).scalar_one_or_none()


def get_by_id(db: Session, resume_file_id: int) -> ResumeFile | None:
    return db.get(ResumeFile, resume_file_id)


def create(
    db: Session,
    *,
    filename: str,
    stored_path: str,
    file_hash: str,
    file_ext: str,
    file_size: int,
    upload_batch_id: str,
    uploaded_by: int,
) -> ResumeFile:
    """新建文件行；`flush()` 让自增主键可用，提交由 service 负责。"""
    row = ResumeFile(
        filename=filename,
        stored_path=stored_path,
        file_hash=file_hash,
        file_ext=file_ext,
        file_size=file_size,
        upload_batch_id=upload_batch_id,
        uploaded_by=uploaded_by,
    )
    db.add(row)
    db.flush()
    return row


def batch_summaries(db: Session, *, offset: int, limit: int) -> list[dict]:
    """按 `upload_batch_id` 汇总（接口 §4 `GET /resumes/batches`）。

    本函数就是"批次"的**唯一来源**——数据库里没有 `upload_batches` 表（决策 D-M），
    批次只是这些行的一个分组；不要再引第二张表来存批次元数据。

    - `file_count` / `success` / `failed` 直接聚合；
    - `pending` = **尚未出结果的数量**（`file_count - success - failed`，含 running /
      duplicate_pending / cancelled），这样三个数与 `file_count` 恒等，前端算进度不会出现
      "加起来对不上"；
    - `uploaded_at` 取批次内最早的 `created_at`（同一次 POST 内逐文件落库，时间差秒级）。
    """
    success = func.sum(case((ParseTask.status == "success", 1), else_=0))
    failed = func.sum(case((ParseTask.status == "failed", 1), else_=0))

    stmt = (
        select(
            ResumeFile.upload_batch_id.label("upload_batch_id"),
            func.count(ResumeFile.id).label("file_count"),
            success.label("success"),
            failed.label("failed"),
            func.min(ResumeFile.created_at).label("uploaded_at"),
        )
        .join(ParseTask, ParseTask.resume_file_id == ResumeFile.id, isouter=True)
        .group_by(ResumeFile.upload_batch_id)
        .order_by(func.min(ResumeFile.created_at).desc(), ResumeFile.upload_batch_id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        {
            "upload_batch_id": row.upload_batch_id,
            "file_count": int(row.file_count or 0),
            "success": int(row.success or 0),
            "failed": int(row.failed or 0),
            "pending": int(row.file_count or 0) - int(row.success or 0) - int(row.failed or 0),
            "uploaded_at": row.uploaded_at,
        }
        for row in rows
    ]


def count_batches(db: Session) -> int:
    """批次数 = `upload_batch_id` 去重数（分页 total 用它，不是文件行数）。"""
    return int(db.execute(select(func.count(func.distinct(ResumeFile.upload_batch_id)))).scalar_one())


def delete_batch_rows(db: Session, upload_batch_id: str) -> int:
    """物理删除某批次（**仅供测试/运维清理**）。

    业务口径是 `resume_files` 行永不物理删除（决策 D-H），生产接口不得调用本函数。
    """
    rows = db.execute(select(ResumeFile).where(ResumeFile.upload_batch_id == upload_batch_id)).scalars().all()
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


# --------------------------------------------------------------------------- #
# T-18 画像详情：画像 ↔ 原始简历（仅追加，既有函数未动）
# --------------------------------------------------------------------------- #
def list_by_talent_profile(db: Session, talent_profile_id: int) -> list[ResumeFile]:
    """画像关联的全部原始简历（经 `parsed_resumes.talent_profile_id` 关联，merge 后可多份）。"""
    return list(
        db.execute(
            select(ResumeFile)
            .join(ParsedResume, ParsedResume.resume_file_id == ResumeFile.id)
            .where(ParsedResume.talent_profile_id == talent_profile_id)
            .order_by(ResumeFile.id)
        ).scalars().all()
    )


def get_for_talent_profile(
    db: Session, talent_profile_id: int, resume_file_id: int
) -> ResumeFile | None:
    """取画像名下的指定简历行；不属于该画像返回 None（下载接口据此 404，防越权遍历他人文件）。"""
    return db.execute(
        select(ResumeFile)
        .join(ParsedResume, ParsedResume.resume_file_id == ResumeFile.id)
        .where(ParsedResume.talent_profile_id == talent_profile_id)
        .where(ResumeFile.id == resume_file_id)
    ).scalar_one_or_none()
