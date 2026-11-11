"""Immutable version extraction and indexing; publication remains explicit."""
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from sqlalchemy import delete, select
from app.core.errors import BizError
from app.database import SessionLocal
from app.modules.knowledge.models import KnowledgeRevision, KnowledgeChunk
from app.modules.knowledge import service, index
from app.modules.knowledge.chunks import make_chunks
from app.modules.knowledge.runtime import Runtime, LeaseLost


def extract(revision):
    path = Path(revision.stored_path)
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != revision.file_hash:
        raise BizError(409, '知识原件缺失或内容校验失败')
    if revision.file_ext == 'txt':
        try:
            return [{'text': path.read_text(encoding='utf-8-sig'), 'page_no': None,
                'source_type': 'txt', 'quality_status': 'ready'}]
        except UnicodeError as exc:
            raise BizError(422, 'TXT文件需要UTF-8编码') from exc
    from app.service.extract_service import extract_text, ExtractError
    try:
        result = extract_text(path, ext=revision.file_ext, max_chars=5_000_000)
        if result.truncated:
            raise BizError(422, '文档超出单次完整处理上限，请拆分原件')
        return [asdict(page) for page in result.pages]
    except ExtractError as exc:
        raise BizError(422, '文档提取失败，请检查原件和页面质量') from exc


def handle_ingest(task_id, token, *, session_factory=None):
    factory = session_factory or SessionLocal
    with Runtime(KnowledgeRevision, task_id, token, factory) as runtime, factory() as db:
        deleting = False
        index_attempted = False
        def guard(*, final=False):
            row = runtime.guard(db)
            service.require_admin(db, row.requested_by)
            doc = service.document(db, row.document_id, lock=final)
            if final:
                db.refresh(row, with_for_update=True)
                if row.status != 'running' or row.lease_token != token:
                    raise LeaseLost()
            if doc.deleted_at is not None and row.stage != 'deleting':
                raise LeaseLost()
            return row, doc
        try:
            row, doc = guard()
            deleting = doc.deleted_at is not None
            if doc.deleted_at is not None:
                index.remove_revision(row.id)
                row, doc = guard(final=True)
                path, root = Path(row.stored_path).resolve(), service.storage_dir().resolve()
                if path.is_relative_to(root):
                    path.unlink(missing_ok=True)
                runtime.write(db, status='completed', stage='deleted', lease_token=None,
                    index_token=None, error=None, finished_at=datetime.now())
                return
            if not runtime.write(db, stage='extracting'):
                return
            pages = extract(row)
            pieces = make_chunks(pages)
            if len(pieces) > 5000:
                raise BizError(422, '文档片段过多，请拆分文件')
            row, doc = guard(final=True)
            existing = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.revision_id == row.id)
                .order_by(KnowledgeChunk.chunk_no)))
            if not existing:
                db.add_all([KnowledgeChunk(revision_id=row.id, **piece) for piece in pieces])
                db.flush()
            if not runtime.write(db, stage='indexing'):
                return
            row, doc = guard()
            chunks = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.revision_id == row.id)
                .order_by(KnowledgeChunk.chunk_no)))
            index_attempted = True
            index.upsert(row, chunks, token, guard=guard)
            guard(final=True)
            def graph_intent():
                # The fenced completion UPDATE and graph intent share one commit.
                if doc.published_revision_id == row.id:
                    from app.modules.talents.graph_service import knowledge_intent
                    db.expire(row)
                    knowledge_intent(db, doc, user_id=row.requested_by)
            runtime.write(db, status='completed', stage='completed', index_token=token,
                lease_token=None, error=None, finished_at=datetime.now(), before_commit=graph_intent)
        except LeaseLost:
            db.rollback()
            if not index_attempted:
                return
            # A remote upsert may finish after deletion cleanup. Queue another
            # deletion pass; only a tombstoned document permits this recovery.
            row = db.get(KnowledgeRevision, task_id)
            if row is not None and service.document(db, row.document_id).deleted_at is not None:
                from sqlalchemy import update
                db.execute(update(KnowledgeRevision).where(KnowledgeRevision.id == task_id).values(
                    status='pending', stage='deleting', lease_token=None, index_token=None,
                    attempt_count=0, finished_at=None))
                db.commit()
        except Exception as exc:
            db.rollback()
            failed = db.get(KnowledgeRevision, task_id)
            runtime.fail(db, exc, stage='deleting' if deleting else (failed.stage if failed else 'extracting'))
