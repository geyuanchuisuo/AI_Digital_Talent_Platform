"""Knowledge authorization and document lifecycle; no model calls in HTTP writes."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, update

from app.config import settings
from app.core.auth.scope import get_account_scope, get_role_codes, MANAGEMENT_ROLES
from app.core.errors import BizError
from app.models.user import User
from app.modules.knowledge.models import KnowledgeDocument, KnowledgeRevision


def storage_dir():
    return settings.upload_dir_path / 'knowledge'


def user(db, user_id):
    account = db.get(User, user_id)
    if account is None or not account.is_active:
        raise BizError(403, '无权访问该资源')
    return account


def is_admin(db, user_id):
    account = user(db, user_id)
    return bool(set(get_role_codes(db, user_id)) & set(MANAGEMENT_ROLES)) and get_account_scope(account).can_view_all_persons


def require_admin(db, user_id):
    if not is_admin(db, user_id):
        raise BizError(403, '仅有效全量管理范围可管理知识文档')


def can_read(db, document, *, user_id):
    user(db, user_id)
    if document.deleted_at is not None or not document.enabled:
        return False
    if document.expires_at is not None and document.expires_at <= datetime.now():
        return False
    return document.access_scope == 'public' or (document.access_scope == 'admin' and is_admin(db, user_id))


def document(db, doc_id, *, lock=False):
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
    value = db.scalar(stmt.with_for_update() if lock else stmt)
    if value is None:
        raise BizError(403, '无权访问该资源')
    return value


def create_document(db, title, access_scope, *, user_id, expires_at=None):
    require_admin(db, user_id)
    if not title.strip() or len(title) > 200 or access_scope not in ('public', 'admin'):
        raise BizError(422, '文档标题或访问范围无效')
    row = KnowledgeDocument(title=title.strip(), access_scope=access_scope,
        uploaded_by=user_id, expires_at=expires_at)
    db.add(row)
    db.commit()
    return row


def upload(db, document_id, filename, content, *, user_id):
    require_admin(db, user_id)
    doc = document(db, document_id, lock=True)
    if doc.deleted_at is not None:
        raise BizError(409, '已删除文档不能上传版本')
    filename = Path(filename or '').name
    ext = Path(filename).suffix.lower().lstrip('.')
    if ext not in ('pdf', 'docx', 'txt') or not content or len(content) > settings.upload_max_mb * 1024 * 1024:
        raise BizError(422, '请选择大小合规的PDF、DOCX或TXT文件')
    if len(filename) > 255:
        raise BizError(422, '文件名过长')
    digest = sha256(content).hexdigest()
    existing = db.scalar(select(KnowledgeRevision).where(KnowledgeRevision.document_id == doc.id,
        KnowledgeRevision.version == doc.latest_version, KnowledgeRevision.file_hash == digest,
        KnowledgeRevision.filename == filename))
    if existing is not None:
        original = Path(existing.stored_path)
        try:
            if (original.resolve().is_relative_to(storage_dir().resolve()) and original.is_file()
                and sha256(original.read_bytes()).hexdigest() == digest):
                return existing
        except OSError:
            pass  # Re-uploading the original creates a new immutable repair version.
    root = storage_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{uuid4().hex}.{ext}'
    path.write_bytes(content)
    doc.latest_version += 1
    row = KnowledgeRevision(document_id=doc.id, version=doc.latest_version, filename=filename,
        stored_path=str(path.resolve()), file_ext=ext, file_size=len(content), file_hash=digest,
        uploaded_by=user_id, requested_by=user_id)
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return row


def publish(db, document_id, revision_id, *, user_id):
    require_admin(db, user_id)
    doc = document(db, document_id, lock=True)
    revision = db.get(KnowledgeRevision, revision_id)
    if doc.deleted_at is not None or revision is None or revision.document_id != doc.id:
        raise BizError(409, '版本不可发布')
    if revision.status != 'completed' or revision.stage != 'completed' or not revision.index_token:
        raise BizError(409, '全文索引尚未就绪，不能发布')
    doc.published_revision_id, doc.published_at, doc.published_by = revision.id, datetime.now(), user_id
    from app.modules.talents.graph_service import knowledge_intent
    knowledge_intent(db, doc, user_id=user_id)
    db.commit()
    return doc


def allowed_revisions(db, *, user_id):
    admin = is_admin(db, user_id)
    now = datetime.now()
    stmt = select(KnowledgeRevision).join(KnowledgeDocument,
        (KnowledgeDocument.published_revision_id == KnowledgeRevision.id) &
        (KnowledgeRevision.document_id == KnowledgeDocument.id)).where(
        KnowledgeDocument.deleted_at.is_(None), KnowledgeDocument.enabled.is_(True),
        (KnowledgeDocument.expires_at.is_(None) | (KnowledgeDocument.expires_at > now)),
        KnowledgeRevision.status == 'completed', KnowledgeRevision.stage == 'completed',
        KnowledgeRevision.index_token.is_not(None))
    stmt = stmt.where(KnowledgeDocument.access_scope.in_(('public', 'admin') if admin else ('public',)))
    return list(db.scalars(stmt.order_by(KnowledgeRevision.id)))


def update_document(db, doc_id, *, user_id, **values):
    require_admin(db, user_id)
    doc = document(db, doc_id, lock=True)
    if doc.deleted_at is not None:
        raise BizError(409, '文档已删除')
    for key, value in values.items():
        if key not in ('access_scope', 'enabled', 'expires_at', 'title'):
            raise BizError(422, '不支持的修改字段')
        if key in ('enabled', 'access_scope', 'title') and value is None:
            raise BizError(422, '字段不能为空')
        if key == 'access_scope' and value not in ('public', 'admin'):
            raise BizError(422, '访问范围无效')
        if key == 'title' and (not value.strip() or len(value) > 200):
            raise BizError(422, '标题无效')
        if key in ('enabled', 'access_scope', 'title') and value is None:
            raise BizError(422, '字段不能为空')
        setattr(doc, key, value)
    if doc.published_revision_id is not None:
        from app.modules.talents.graph_service import knowledge_intent
        knowledge_intent(db, doc, user_id=user_id)
    db.commit()
    return doc


def delete_document(db, doc_id, *, user_id):
    require_admin(db, user_id)
    doc = document(db, doc_id, lock=True)
    if doc.deleted_at is not None:
        return doc
    doc.deleted_at = doc.deleted_at or datetime.now()
    doc.enabled, doc.published_revision_id = False, None
    from app.modules.talents.graph_service import knowledge_intent
    knowledge_intent(db, doc, user_id=user_id)
    # Queue removal on each immutable revision; old running writers lose their lease.
    db.execute(update(KnowledgeRevision).where(KnowledgeRevision.document_id == doc_id).values(
        status='pending', stage='deleting', lease_token=None, index_token=None,
        attempt_count=0, requested_by=user_id, finished_at=None, error=None))
    db.commit()
    return doc


def retry(db, revision_id, *, user_id):
    require_admin(db, user_id)
    row = db.get(KnowledgeRevision, revision_id)
    if row is None:
        raise BizError(403, '无权访问该资源')
    doc = document(db, row.document_id, lock=True)
    db.refresh(row, with_for_update=True)
    if row.status in ('pending', 'running'):
        return row
    row.status, row.stage = 'pending', 'deleting' if doc.deleted_at else 'queued'
    row.lease_token, row.index_token = None, None
    row.attempt_count, row.requested_by = 0, user_id
    row.finished_at, row.error = None, None
    if doc.published_revision_id == row.id:
        from app.modules.talents.graph_service import knowledge_intent
        knowledge_intent(db, doc, user_id=user_id)
    db.commit()
    return row
