"""B09 explicit confirmation and current, authorized source reconstruction."""
from datetime import datetime
from sqlalchemy import select
from app.core.errors import BizError
from app.modules.talents.question_jobs import current_scope
from app.modules.talents.graph_models import GraphSource,GraphSyncJob
from app.modules.talents.graph_outbox import enqueue
from app.modules.talents.graph_projection import profile_projection,policy_projection,job_projection,MAX_PARTS
from app.service.talent_service import get_person_snapshot,SnapshotScopeError


def require_management(db,uid):
    from app.modules.knowledge.service import require_admin
    require_admin(db,uid)


def profile_source(db,pid,version,uid):
    from app.modules.talents.history import current_version
    try:
        snapshot=get_person_snapshot(db,pid,version=version,scope=current_scope(db,uid))
    except SnapshotScopeError as exc:
        raise BizError(403,'无权访问该资源') from exc
    latest=current_version(db,pid)
    if snapshot['status']!='ready' or latest is None or latest.version!=version:
        raise BizError(409,'档案版本或来源已变化，请重新确认')
    return profile_projection(snapshot)


def confirm_profile(db,pid,version,*,user_id,resync=False):
    from app.models.talent import TalentProfile
    require_management(db,user_id)
    person=db.scalar(select(TalentProfile).where(TalentProfile.id==pid).with_for_update())
    if person is None or person.deleted_at is not None:
        raise BizError(403,'无权访问该资源')
    profile_source(db,pid,version,user_id)
    job=enqueue(db,'profile',pid,version,operation='upsert',requested_by=user_id,force=resync)
    db.commit()
    return job_view(job)


def knowledge_intent(db,doc,*,user_id):
    from app.modules.knowledge.models import KnowledgeRevision
    revision=db.get(KnowledgeRevision,doc.published_revision_id) if doc.published_revision_id else None
    valid=(doc.deleted_at is None and doc.enabled and
        (doc.expires_at is None or doc.expires_at>datetime.now()) and revision is not None and
        revision.document_id==doc.id and revision.status=='completed' and revision.stage=='completed' and revision.index_token)
    return enqueue(db,'knowledge',doc.id,revision.version if revision else doc.latest_version,
        operation='upsert' if valid else 'invalidate',requested_by=user_id,force=True)


def source_projection(db,job):
    require_management(db,job.requested_by)
    if job.source_kind=='profile':
        return profile_source(db,job.source_id,job.source_version,job.requested_by)
    if job.source_kind=='job':
        from app.service.job_profile_service import graph_source
        return job_projection(graph_source(db,job.source_id,job.source_version,user_id=job.requested_by))
    from app.modules.knowledge import service
    from app.modules.knowledge.models import KnowledgeRevision,KnowledgeChunk
    doc=service.document(db,job.source_id)
    if not service.can_read(db,doc,user_id=job.requested_by) or not doc.published_revision_id:
        raise BizError(409,'知识来源当前不可用')
    rev=db.get(KnowledgeRevision,doc.published_revision_id)
    if (rev is None or rev.document_id!=doc.id or rev.version!=job.source_version or
        rev.status!='completed' or rev.stage!='completed' or not rev.index_token):
        raise BizError(409,'知识版本已变化')
    chunks=[{'id':c.id,'page_no':c.page_no,'offset_start':c.offset_start,'text':c.text}
        for c in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.revision_id==rev.id)
            .order_by(KnowledgeChunk.chunk_no,KnowledgeChunk.id).limit(MAX_PARTS+1))]
    return policy_projection(document_id=doc.id,revision_id=rev.id,version=rev.version,title=doc.title,chunks=chunks)


def job_view(job):
    from app.config import settings
    return dict(task_id=job.id,generation=job.generation,source_version=job.source_version,
        operation=job.operation,status=job.status,stage=job.stage,attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,graph_enabled=settings.neo4j_enabled)


def state_for(db,kind,source_id):
    state=db.scalar(select(GraphSource).where(GraphSource.source_kind==kind,GraphSource.source_id==source_id))
    if state is None:return None,None
    job=db.scalar(select(GraphSyncJob).where(GraphSyncJob.source_kind==kind,GraphSyncJob.source_id==source_id,
        GraphSyncJob.generation==state.generation))
    return state,job
