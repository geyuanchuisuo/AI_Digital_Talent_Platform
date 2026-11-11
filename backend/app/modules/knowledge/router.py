"""Institutional knowledge API; every read consumes current server authorization."""
from datetime import datetime
from typing import Annotated
from pathlib import Path
from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.api.deps import CurrentUser
from app.config import settings
from app.core.errors import BizError
from app.core.response import ok
from app.database import get_db
from app.modules.knowledge import service, questions
from app.modules.knowledge.models import KnowledgeDocument, KnowledgeRevision

router = APIRouter(prefix='/api/knowledge', tags=['knowledge'])
DB = Annotated[Session, Depends(get_db)]


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    access_scope: str = 'admin'
    expires_at: datetime | None = None

    @field_validator('expires_at')
    @classmethod
    def local_time(cls, value):
        return value.astimezone().replace(tzinfo=None) if value is not None and value.tzinfo else value


class DocumentUpdate(DocumentCreate):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    access_scope: str | None = None
    enabled: bool | None = None


class Publish(BaseModel):
    revision_id: int = Field(ge=1)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=64)


def revision_view(row):
    return {'id': row.id, 'version': row.version, 'filename': row.filename, 'file_size': row.file_size,
        'status': row.status, 'stage': row.stage, 'attempt_count': row.attempt_count,
        'max_attempts': row.max_attempts, 'error': row.error}


def document_view(db, row, *, admin):
    stmt = select(KnowledgeRevision).where(KnowledgeRevision.document_id == row.id)
    if not admin:
        stmt = stmt.where(KnowledgeRevision.id == row.published_revision_id)
    revisions = list(db.scalars(stmt.order_by(KnowledgeRevision.version.desc())))
    return {'id': row.id, 'title': row.title, 'access_scope': row.access_scope,
        'latest_version': row.latest_version, 'published_revision_id': row.published_revision_id,
        'published_at': row.published_at, 'published_by': row.published_by,
        'enabled': row.enabled, 'expires_at': row.expires_at, 'deleted_at': row.deleted_at,
        'versions': [revision_view(revision) for revision in revisions]}


@router.get('/documents')
def list_documents(user: CurrentUser, db: DB, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    admin = service.is_admin(db, user.id)
    condition = []
    if not admin:
        condition.append(KnowledgeDocument.id.in_([r.document_id for r in service.allowed_revisions(db, user_id=user.id)]))
    total = db.scalar(select(func.count()).select_from(KnowledgeDocument).where(*condition)) or 0
    rows = db.scalars(select(KnowledgeDocument).where(*condition).order_by(KnowledgeDocument.id.desc())
        .offset(offset).limit(limit)).all()
    return ok({'items': [document_view(db, row, admin=admin) for row in rows], 'total': total, 'can_manage': admin})


@router.post('/documents')
def create_document(payload: DocumentCreate, user: CurrentUser, db: DB):
    row = service.create_document(db, payload.title, payload.access_scope, user_id=user.id, expires_at=payload.expires_at)
    return ok(document_view(db, row, admin=True))


@router.get('/documents/{document_id}/graph')
def get_knowledge_graph(document_id: int, user: CurrentUser, db: DB):
    from app.modules.talents.graph_read import read
    response = ok(read(db, 'knowledge', document_id, user_id=user.id))
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/documents/{document_id}/graph-preview')
def get_knowledge_graph_preview(document_id: int, user: CurrentUser, db: DB):
    from app.modules.knowledge.graph_preview import preview
    response = ok(preview(db, document_id, user_id=user.id))
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.patch('/documents/{document_id}')
def update_document(document_id: int, payload: DocumentUpdate, user: CurrentUser, db: DB):
    row = service.update_document(db, document_id, user_id=user.id, **payload.model_dump(exclude_unset=True))
    return ok(document_view(db, row, admin=True))


@router.post('/documents/{document_id}/versions')
def upload_version(document_id: int, user: CurrentUser, db: DB, file: Annotated[UploadFile, File()]):
    service.require_admin(db, user.id)
    content = file.file.read(settings.upload_max_mb * 1024 * 1024 + 1)
    row = service.upload(db, document_id, file.filename, content, user_id=user.id)
    return ok(revision_view(row))


@router.post('/documents/{document_id}/publish')
def publish_document(document_id: int, payload: Publish, user: CurrentUser, db: DB):
    return ok(document_view(db, service.publish(db, document_id, payload.revision_id, user_id=user.id), admin=True))


@router.delete('/documents/{document_id}')
def delete_document(document_id: int, user: CurrentUser, db: DB):
    service.delete_document(db, document_id, user_id=user.id)
    return ok({'id': document_id, 'deleted': True})


@router.post('/versions/{revision_id}/retry')
def retry_version(revision_id: int, user: CurrentUser, db: DB):
    return ok(revision_view(service.retry(db, revision_id, user_id=user.id)))


@router.get('/versions/{revision_id}/source')
def download_source(revision_id: int, user: CurrentUser, db: DB):
    row = db.get(KnowledgeRevision, revision_id)
    if row is None:
        raise BizError(403, '无权访问该资源')
    doc = service.document(db, row.document_id)
    admin = service.is_admin(db, user.id)
    if doc.deleted_at is not None or (not admin and (not service.can_read(db, doc, user_id=user.id)
        or doc.published_revision_id != row.id or row.status != 'completed' or row.stage != 'completed')):
        raise BizError(403, '无权访问该资源')
    path = Path(row.stored_path).resolve()
    if not path.is_relative_to(service.storage_dir().resolve()) or not path.is_file():
        raise BizError(404, '原件当前不可用')
    return FileResponse(path, filename=row.filename, media_type='application/octet-stream',
        headers={'Cache-Control': 'private, no-store'})


@router.post('/questions')
def create_question(payload: Question, user: CurrentUser, db: DB):
    return ok(questions.create(db, payload.question, payload.idempotency_key, user_id=user.id))


@router.get('/questions/recent')
def recent_question(user: CurrentUser, db: DB):
    return ok(questions.recent(db, user_id=user.id))


@router.get('/questions/{job_id}')
def read_question(job_id: int, user: CurrentUser, db: DB):
    return ok(questions.read(db, job_id, user_id=user.id))


@router.post('/questions/{job_id}/cancel')
def cancel_question(job_id: int, user: CurrentUser, db: DB):
    return ok(questions.cancel(db, job_id, user_id=user.id))
