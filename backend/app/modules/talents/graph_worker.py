"""SQL-fenced outbox delivery. Cross-store consistency is eventual, not atomic."""
from datetime import datetime
from sqlalchemy import select
from app.database import SessionLocal
from app.core.errors import BizError
from app.modules.knowledge.runtime import Runtime,LeaseLost
from app.modules.talents.graph_models import GraphSource,GraphSyncJob
from app.modules.talents.graph_service import source_projection
from app.modules.talents.graph_runtime import get_client
from app.modules.talents.graph_outbox import enqueue


def handle_graph_sync(task_id,token,*,session_factory=None,client=None):
    factory=session_factory or SessionLocal
    with Runtime(GraphSyncJob,task_id,token,factory) as runtime,factory() as db:
        def guard():
            job=runtime.guard(db)
            state=db.scalar(select(GraphSource).where(GraphSource.source_kind==job.source_kind,
                GraphSource.source_id==job.source_id))
            if state is None or state.generation!=job.generation:raise LeaseLost()
            return job
        try:
            job=guard()
            projection=None
            if job.operation=='upsert':
                try:
                    projection=source_projection(db,job)
                except BizError as exc:
                    if exc.code not in (403,404,409):raise
                    # Remove stale materialization even if the submitter lost permission.
                    job=guard()
                    state=db.scalar(select(GraphSource).where(GraphSource.source_kind==job.source_kind,
                        GraphSource.source_id==job.source_id).with_for_update())
                    if state.generation!=job.generation:raise LeaseLost()
                    enqueue(db,job.source_kind,job.source_id,job.source_version,
                        operation='invalidate',requested_by=job.requested_by,force=True)
                    db.commit()
                    return
            identity=(job.source_kind,job.source_id,job.generation)
            expected=projection['fingerprint'] if projection else None
            db.rollback()  # Never hold SQL source locks while waiting on Neo4j.
            transport=client or get_client()
            if not transport.replace(*identity,projection):
                raise BizError(409,'图谱已有更新的同步序号，需对账')
            job=guard()
            if projection is not None:
                try:
                    fresh=source_projection(db,job)
                    if fresh['fingerprint']!=expected:raise BizError(409,'来源已变化')
                except BizError as exc:
                    if exc.code not in (403,404,409):raise
                    job=guard()
                    state=db.scalar(select(GraphSource).where(GraphSource.source_kind==job.source_kind,
                        GraphSource.source_id==job.source_id).with_for_update())
                    if state.generation!=job.generation:raise LeaseLost()
                    enqueue(db,job.source_kind,job.source_id,job.source_version,
                        operation='invalidate',requested_by=job.requested_by,force=True)
                    db.commit();return
            runtime.write(db,status='completed',stage='completed',lease_token=None,
                fingerprint=expected,error=None,finished_at=datetime.now())
        except LeaseLost:
            db.rollback()  # New generation already has its own durable compensation intent.
        except Exception as exc:
            runtime.fail(db,exc,stage='syncing')
