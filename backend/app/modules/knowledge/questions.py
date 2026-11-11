"""Durable, owner-bound institutional QA and current citation authorization."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from app.core.errors import BizError
from app.modules.knowledge.models import KnowledgeQuestionJob, KnowledgeRevision, KnowledgeChunk
from app.modules.knowledge import service


def verify_context(db, context, *, user_id, lock=False):
    service.user(db, user_id)
    documents = {c['document_id'] for c in context}
    loaded = {did: service.document(db, did, lock=lock) for did in sorted(documents)}
    for citation in context:
        doc = loaded[citation['document_id']]
        revision = db.get(KnowledgeRevision, citation['revision_id'])
        chunk = db.get(KnowledgeChunk, citation['chunk_id'])
        if (not service.can_read(db, doc, user_id=user_id) or doc.published_revision_id != citation['revision_id']
            or revision is None or revision.document_id != doc.id or revision.status != 'completed'
            or revision.stage != 'completed' or revision.version != citation['version']
            or chunk is None or chunk.revision_id != revision.id or chunk.text != citation['snippet']):
            raise BizError(409, '知识引用来源当前不可访问')


def view(db, row, *, user_id):
    service.user(db, user_id)
    if row is None or row.asked_by != user_id:
        raise BizError(403, '无权访问该资源')
    result = deepcopy(row.progress_json.get('result'))
    status = row.status
    if result is not None:
        try:
            verify_context(db, result.get('citations', []), user_id=user_id)
        except BizError:
            result, status = None, 'source_unavailable'
    return {'job_id': row.id, 'question': row.request_json['question'], 'status': status,
        'stage': row.stage, 'attempt_count': row.attempt_count, 'max_attempts': row.max_attempts,
        'result': result, 'error': row.error}


def read(db, job_id, *, user_id):
    return view(db, db.get(KnowledgeQuestionJob, job_id), user_id=user_id)


def recent(db, *, user_id):
    service.user(db, user_id)
    row = db.scalar(select(KnowledgeQuestionJob).where(KnowledgeQuestionJob.asked_by == user_id)
        .order_by(KnowledgeQuestionJob.id.desc()).limit(1))
    return None if row is None else view(db, row, user_id=user_id)


def create(db, question, key, *, user_id):
    service.user(db, user_id)
    question = question.strip()
    if not question or len(question) > 1000 or not key or len(key) > 64:
        raise BizError(422, '问题或幂等键无效')
    fingerprint = sha256(question.encode()).hexdigest()
    existing = db.scalar(select(KnowledgeQuestionJob).where(
        KnowledgeQuestionJob.asked_by == user_id, KnowledgeQuestionJob.idempotency_key == key))
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise BizError(409, '该幂等键已用于其他问题')
        return view(db, existing, user_id=user_id)
    row = KnowledgeQuestionJob(asked_by=user_id, idempotency_key=key, request_hash=fingerprint,
        request_json={'question': question}, progress_json={}, audit_json={})
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(KnowledgeQuestionJob).where(
            KnowledgeQuestionJob.asked_by == user_id, KnowledgeQuestionJob.idempotency_key == key))
        if existing is None:
            raise
        if existing.request_hash != fingerprint:
            raise BizError(409, '该幂等键已用于其他问题')
        return view(db, existing, user_id=user_id)
    return view(db, row, user_id=user_id)


def cancel(db, job_id, *, user_id):
    row = db.get(KnowledgeQuestionJob, job_id)
    if row is None or row.asked_by != user_id:
        raise BizError(403, '无权访问该资源')
    db.execute(update(KnowledgeQuestionJob).where(KnowledgeQuestionJob.id == job_id,
        KnowledgeQuestionJob.status.in_(['pending', 'running'])).values(
            status='cancelled', stage='cancelled', lease_token=None, finished_at=datetime.now()))
    db.commit()
    return read(db, job_id, user_id=user_id)
