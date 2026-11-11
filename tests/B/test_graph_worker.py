from contextlib import contextmanager
from sqlalchemy import select
import pytest
from test_graph_outbox import graph_db


@pytest.fixture
def worker(graph_db,monkeypatch):
    from app.modules.knowledge.runtime import Runtime
    from app.modules.talents import graph_worker
    from app.modules.talents.graph_projection import job_projection
    from app.modules.talents.graph_outbox import enqueue
    monkeypatch.setattr(Runtime,'__enter__',lambda self:self)
    monkeypatch.setattr(Runtime,'__exit__',lambda *args:None)
    projection=job_projection(dict(job_id=3,version=1,title='合成岗位',jd_text='Python'))
    monkeypatch.setattr(graph_worker,'source_projection',lambda *args:projection)
    row=enqueue(graph_db,'job',3,1,operation='upsert',requested_by=7)
    row.status='running';row.lease_token='valid';row.attempt_count=1
    graph_db.commit()
    @contextmanager
    def factory():yield graph_db
    def run(client,token='valid'):
        graph_worker.handle_graph_sync(row.id,token,session_factory=factory,client=client)
    return graph_db,row,run,graph_worker


def test_success_is_recorded_only_after_remote_write(worker):
    db,row,run,_=worker
    class Transport:
        def replace(self,*args):
            assert row.status=='running' and row.fingerprint is None
            assert args[:3]==('job',3,1)
            return True
    run(Transport())
    db.refresh(row)
    assert row.status=='completed' and row.fingerprint and row.lease_token is None


def test_lost_lease_never_calls_remote(worker):
    _,row,run,_=worker
    class Transport:
        def replace(self,*args):pytest.fail('stale lease wrote graph')
    run(Transport(),token='old')
    assert row.status=='running'


def test_dependency_failure_retries_finitely_without_exposing_secret(worker):
    db,row,run,_=worker
    class Transport:
        def replace(self,*args):raise RuntimeError('secret-credential')
    run(Transport())
    db.refresh(row)
    assert row.status=='pending' and 'secret-credential' not in row.error
    row.status='running';row.lease_token='valid';row.attempt_count=3;db.commit()
    run(Transport());db.refresh(row)
    assert row.status=='failed' and row.attempt_count==3


def test_delete_during_remote_write_cannot_publish_old_completion(worker):
    from app.modules.talents.graph_outbox import enqueue
    from app.modules.talents.graph_models import GraphSyncJob
    db,row,run,_=worker
    class Transport:
        def replace(self,*args):
            enqueue(db,'job',3,1,operation='invalidate',force=True)
            db.commit()
            return True
    run(Transport());db.refresh(row)
    assert row.status=='cancelled' and row.fingerprint is None
    latest=db.scalar(select(GraphSyncJob).order_by(GraphSyncJob.id.desc()))
    assert latest.generation==2 and latest.operation=='invalidate' and latest.status=='pending'


def test_revoke_after_remote_write_queues_removal(worker,monkeypatch):
    from app.core.errors import BizError
    from app.modules.talents.graph_models import GraphSyncJob
    db,row,run,module=worker
    class Transport:
        def replace(self,*args):
            def revoked(*a):raise BizError(403,'无权访问该资源')
            monkeypatch.setattr(module,'source_projection',revoked)
            return True
    run(Transport());db.refresh(row)
    assert row.status=='cancelled'
    latest=db.scalar(select(GraphSyncJob).order_by(GraphSyncJob.id.desc()))
    assert latest.operation=='invalidate' and latest.generation==2
