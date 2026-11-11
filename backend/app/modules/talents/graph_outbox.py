"""Caller owns source lock and commit. Duplicate intent preserves retry budget."""
from sqlalchemy import select, update
from app.modules.talents.graph_models import GraphSource, GraphSyncJob


def enqueue(db, kind, source_id, version, *, operation, requested_by=None, force=False):
    if kind not in ('profile','knowledge','job') or operation not in ('upsert','invalidate'):
        raise ValueError('Invalid graph intent')
    state = db.scalar(select(GraphSource).where(GraphSource.source_kind==kind,
        GraphSource.source_id==source_id).with_for_update())
    if state is not None and not force and (state.source_version,state.operation)==(version,operation):
        return db.scalar(select(GraphSyncJob).where(GraphSyncJob.source_kind==kind,
            GraphSyncJob.source_id==source_id,GraphSyncJob.generation==state.generation))
    if state is None:
        state = GraphSource(source_kind=kind,source_id=source_id,source_version=version,generation=0,operation=operation)
        db.add(state)
    state.generation += 1
    state.source_version, state.operation = version, operation
    db.execute(update(GraphSyncJob).where(GraphSyncJob.source_kind==kind,GraphSyncJob.source_id==source_id,
        GraphSyncJob.status.in_(('pending','running'))).values(status='cancelled',lease_token=None))
    row = GraphSyncJob(source_kind=kind,source_id=source_id,source_version=version,generation=state.generation,
        operation=operation,requested_by=requested_by)
    db.add(row); db.flush()
    return row


def invalidate_existing(db,kind,source_id,version,*,requested_by=None):
    # No need to allocate graph state for sources that were never confirmed/indexed.
    if db.scalar(select(GraphSource.id).where(GraphSource.source_kind==kind,GraphSource.source_id==source_id)):
        return enqueue(db,kind,source_id,version,operation='invalidate',requested_by=requested_by,force=True)
