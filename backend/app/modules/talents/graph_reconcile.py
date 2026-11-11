"""Bounded SQL lifecycle reconciliation, including expiry without an HTTP write.

This is not a remote graph integrity audit. Remote discrepancies remain visible
as inconsistent and require explicit resynchronization; failed budgets stay finite.
"""
import logging
from threading import Event,Thread
from sqlalchemy import select
from app.config import settings
from app.core.errors import BizError
from app.database import SessionLocal
from app.modules.talents.graph_models import GraphSource,GraphSyncJob
from app.modules.talents.graph_service import source_projection
from app.modules.talents.graph_outbox import enqueue

logger=logging.getLogger(__name__)


def reconcile_page(*,session_factory=None,cursor=0,limit=100,stop=None):
    factory=session_factory or SessionLocal
    limit=max(1,min(100,limit))
    with factory() as db:
        ids=list(db.scalars(select(GraphSource.id).where(GraphSource.id>cursor)
            .order_by(GraphSource.id).limit(limit)))
    last=cursor
    for source_id in ids:
        if stop is not None and stop.is_set():return last
        with factory() as db:
            state=db.scalar(select(GraphSource).where(GraphSource.id==source_id).with_for_update())
            last=source_id
            if state is None or state.operation!='upsert':
                db.rollback();continue
            job=db.scalar(select(GraphSyncJob).where(GraphSyncJob.source_kind==state.source_kind,
                GraphSyncJob.source_id==state.source_id,GraphSyncJob.generation==state.generation))
            if job is None:
                db.rollback();continue
            try:
                projection=source_projection(db,job)
            except BizError as exc:
                if exc.code>=500 or exc.code==429:
                    db.rollback()
                    logger.warning('Graph source temporarily unavailable; continuing lifecycle sweep')
                    continue
                # Permanent per-source errors (including 422 bounds) cannot
                # starve later sources, and stale graph content must be removed.
                enqueue(db,state.source_kind,state.source_id,state.source_version,
                    operation='invalidate',requested_by=job.requested_by,force=True)
            except (KeyError,TypeError,ValueError):
                enqueue(db,state.source_kind,state.source_id,state.source_version,
                    operation='invalidate',requested_by=job.requested_by,force=True)
            else:
                if job.status=='completed' and job.fingerprint and job.fingerprint!=projection['fingerprint']:
                    enqueue(db,state.source_kind,state.source_id,state.source_version,
                        operation='upsert',requested_by=job.requested_by,force=True)
            db.commit()
    return last if len(ids)==limit else 0


class GraphReconciler:
    def __init__(self):
        self.stop_event=Event()
        self.thread=None

    def start(self):
        if self.thread is not None and self.thread.is_alive():return
        self.stop_event.clear()
        self.thread=Thread(target=self._run,name='b09-graph-reconcile',daemon=True)
        self.thread.start()

    def _run(self):
        cursor=0
        while not self.stop_event.wait(30):
            if not settings.neo4j_enabled:continue
            try:
                cursor=reconcile_page(cursor=cursor,stop=self.stop_event)
            except Exception:
                # No credentials/source text from driver/DB exceptions in logs.
                logger.warning('Graph SQL reconciliation unavailable; next sweep will retry')

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:self.thread.join(timeout=2)


reconciler=GraphReconciler()
