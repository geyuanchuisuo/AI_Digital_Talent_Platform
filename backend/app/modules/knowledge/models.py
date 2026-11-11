"""B-owned versioned institutional knowledge and durable task models."""
from datetime import datetime
from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.base import PKMixin, PK_TYPE, UpdatedAtMixin, CreatedAtMixin, MYSQL_TABLE_OPTIONS, JSONDict, num_default, str_default


class KnowledgeDocument(PKMixin, UpdatedAtMixin, Base):
    __tablename__ = 'knowledge_documents'
    __table_args__ = (MYSQL_TABLE_OPTIONS,)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    access_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    uploaded_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=num_default(0))
    published_revision_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    published_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=num_default(1))
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class _LeaseTask:
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', server_default=str_default('pending'))
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default='queued', server_default=str_default('queued'))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=num_default(0))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default=num_default(3))
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeRevision(PKMixin, UpdatedAtMixin, _LeaseTask, Base):
    __tablename__ = 'knowledge_revisions'
    __table_args__ = (UniqueConstraint('document_id', 'version', name='uk_knowledge_version'),
        Index('idx_knowledge_ingest_queue', 'status', 'id'), MYSQL_TABLE_OPTIONS)
    document_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(10), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    requested_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    index_token: Mapped[str | None] = mapped_column(String(36), nullable=True)


class KnowledgeChunk(PKMixin, CreatedAtMixin, Base):
    __tablename__ = 'knowledge_chunks'
    __table_args__ = (UniqueConstraint('revision_id', 'chunk_no', name='uk_knowledge_chunk'), MYSQL_TABLE_OPTIONS)
    revision_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False, index=True)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    offset_start: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeQuestionJob(PKMixin, UpdatedAtMixin, _LeaseTask, Base):
    __tablename__ = 'knowledge_question_jobs'
    __table_args__ = (UniqueConstraint('asked_by', 'idempotency_key', name='uk_knowledge_question_key'),
        Index('idx_knowledge_qa_queue', 'status', 'id'), MYSQL_TABLE_OPTIONS)
    asked_by: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    progress_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)
    audit_json: Mapped[dict] = mapped_column(JSONDict, nullable=False)


KNOWLEDGE_MODELS = (KnowledgeDocument, KnowledgeRevision, KnowledgeChunk, KnowledgeQuestionJob)
