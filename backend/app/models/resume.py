"""简历与解析域：`resume_files` / `parse_tasks` / `parsed_resumes`（数据库设计 v1.2 §4）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    BOOL_TYPE,
    JSONDict,
    MEDIUMTEXT_TYPE,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    PK_TYPE,
    TINY_TYPE,
    UINT_TYPE,
    CreatedAtMixin,
    UpdatedAtMixin,
    num_default,
    str_default,
)


class ResumeFile(PKMixin, CreatedAtMixin, Base):
    """§4.1：file_hash 在上传阶段去重；upload_batch_id 为 F1-6 筛选范围依据。

    ⚠️ **没有 `upload_batches` 表**（决策 D-M）：`upload_batch_id` 只是本表上的一个
    CHAR(26) 字段，一次批量上传共用一个 ULID；批次汇总 = 按该字段 GROUP BY
    （`dao/resume_files.batch_summaries`）。**不要为批次补表/补外键**，一期 13 张表
    是冻结口径，`app/models/__init__.py` 里有断言拦着。
    """

    __tablename__ = "resume_files"
    __table_args__ = (
        UniqueConstraint("file_hash", name="uk_file_hash"),
        Index("idx_upload_batch", "upload_batch_id"),
        Index("idx_uploaded_by", "uploaded_by"),
        MYSQL_TABLE_OPTIONS,
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size: Mapped[int] = mapped_column(UINT_TYPE, nullable=False)
    upload_batch_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)  # 批次唯一载体，无批次表（D-M）
    uploaded_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)


class ParseTask(PKMixin, UpdatedAtMixin, Base):
    """§4.2 解析队列，与 resume_files 1:1。

    `stage` 是断点续跑起点（retry 保留，仅重置 status/error）；
    status 含 `cancelled`（DELETE 队列任务置 cancelled，不物理删行）。
    """

    __tablename__ = "parse_tasks"
    __table_args__ = (
        UniqueConstraint("resume_file_id", name="uk_resume_file"),
        Index("idx_status", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    resume_file_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=str_default("pending")
    )
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retries: Mapped[int] = mapped_column(
        TINY_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    truncated: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=0, server_default=num_default(0)
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    profiled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ParsedResume(PKMixin, CreatedAtMixin, Base):
    """§4.3：resume_file_id UNIQUE——重试按该键整体更新，严禁插第二行。

    structured_json 为 ResumeSchema（dict）；raw_text 为截断后的 MEDIUMTEXT。
    """

    __tablename__ = "parsed_resumes"
    __table_args__ = (
        UniqueConstraint("resume_file_id", name="uk_parsed_resume_file"),
        Index("idx_parsed_talent", "talent_profile_id"),
        MYSQL_TABLE_OPTIONS,
    )

    resume_file_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    talent_profile_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    structured_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    raw_text: Mapped[str] = mapped_column(MEDIUMTEXT_TYPE, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
