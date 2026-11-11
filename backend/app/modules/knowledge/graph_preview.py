"""Only currently published, authorized institutional sources can be projected."""
from sqlalchemy import select

from app.core.errors import BizError
from app.modules.knowledge import service
from app.modules.knowledge.models import KnowledgeRevision, KnowledgeChunk
from app.modules.talents.graph_projection import policy_projection, MAX_PARTS


def _source(db, document_id, user_id):
    db.rollback()
    service.user(db, user_id)
    doc = service.document(db, document_id)
    if not service.can_read(db, doc, user_id=user_id) or doc.published_revision_id is None:
        raise BizError(403, '无权访问该资源')
    revision = db.get(KnowledgeRevision, doc.published_revision_id)
    if (revision is None or revision.document_id != doc.id or revision.status != 'completed'
        or revision.stage != 'completed' or not revision.index_token):
        raise BizError(409, '知识文档版本当前不可用')
    return doc, revision


def preview(db, document_id, *, user_id):
    doc, revision = _source(db, document_id, user_id)
    expected = (revision.id, revision.version, revision.index_token)
    chunks = [{'id':c.id, 'page_no':c.page_no, 'offset_start':c.offset_start, 'text':c.text,
               'source_type':c.source_type} for c in db.scalars(select(KnowledgeChunk).where(
                   KnowledgeChunk.revision_id==revision.id).order_by(KnowledgeChunk.chunk_no,KnowledgeChunk.id).limit(MAX_PARTS+1))]
    result = policy_projection(document_id=doc.id, revision_id=revision.id,
        version=revision.version, title=doc.title, chunks=chunks)
    _, current = _source(db, document_id, user_id)
    if (current.id,current.version,current.index_token) != expected:
        raise BizError(409, '知识发布版本已变化，请重新预览')
    return result
