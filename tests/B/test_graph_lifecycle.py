from test_round1_versions import db
from test_knowledge_service import knowledge
from test_graph_preview import prepared, published, SCOPE
from sqlalchemy import select
import pytest


def test_profile_requires_explicit_current_version_and_reconfirmation(db,monkeypatch):
    from app.modules.talents import graph_service as service
    from app.modules.talents.graph_models import GraphSource
    from app.modules.talents.history import save_version
    from app.core.errors import BizError
    person,_ = prepared(db)
    monkeypatch.setattr(service,'require_management',lambda *a:None)
    monkeypatch.setattr(service,'current_scope',lambda *a:SCOPE)
    with pytest.raises(BizError):service.confirm_profile(db,person.id,2,user_id=7)
    db.rollback()
    result = service.confirm_profile(db,person.id,1,user_id=7)
    assert result['status']=='pending' and result['generation']==1
    person.city='合成新城市'
    save_version(db,person,'manual_edit',changed_by=7);db.commit()
    state = db.scalar(select(GraphSource))
    assert state.operation=='invalidate' and state.source_version==2
    service.confirm_profile(db,person.id,2,user_id=7)
    assert state.operation=='upsert' and state.generation==3
    service.confirm_profile(db,person.id,2,user_id=7,resync=True)
    assert state.generation==4


def test_policy_publication_change_and_delete_enqueue_in_same_transaction(knowledge):
    from app.modules.talents.graph_models import GraphSource,GraphSyncJob
    db,service,doc,revision=published(knowledge)
    state = db.scalar(select(GraphSource))
    assert state.operation=='upsert' and state.source_version==revision.version
    service.update_document(db,doc.id,user_id=7,access_scope='admin')
    assert state.generation==2  # Same revision, different authorization generation.
    service.delete_document(db,doc.id,user_id=7)
    assert state.generation==3 and state.operation=='invalidate'
    rows=list(db.scalars(select(GraphSyncJob).order_by(GraphSyncJob.id)))
    assert [r.status for r in rows]==['cancelled','cancelled','pending']


def test_reindex_published_policy_invalidates_then_requeues_atomically(knowledge,monkeypatch):
    from app.modules.talents.graph_models import GraphSource
    from app.modules.knowledge import ingest,index
    from app.modules.knowledge.runtime import Runtime
    from contextlib import contextmanager
    db,service,doc,revision=published(knowledge)
    state=db.scalar(select(GraphSource))
    revision.status='failed';db.commit()
    service.retry(db,revision.id,user_id=7)
    assert state.operation=='invalidate' and state.generation==2
    revision.status='running';revision.lease_token='new-lease';revision.attempt_count=1;db.commit()
    monkeypatch.setattr(Runtime,'__enter__',lambda self:self)
    monkeypatch.setattr(Runtime,'__exit__',lambda *args:None)
    monkeypatch.setattr(index,'upsert',lambda *a,**kw:None)
    @contextmanager
    def factory():yield db
    ingest.handle_ingest(revision.id,'new-lease',session_factory=factory)
    db.refresh(revision);db.refresh(state)
    assert revision.status=='completed' and revision.index_token=='new-lease'
    assert state.operation=='upsert' and state.generation==3


def test_expiry_reconciliation_enqueues_tombstone_without_remote_calls(knowledge):
    from datetime import datetime,timedelta
    from contextlib import contextmanager
    from app.modules.talents.graph_models import GraphSource
    from app.modules.talents.graph_reconcile import reconcile_page
    db,_,doc,_=published(knowledge)
    doc.expires_at=datetime.now()-timedelta(seconds=1);db.commit()
    @contextmanager
    def factory():yield db
    reconcile_page(session_factory=factory)
    state=db.scalar(select(GraphSource))
    assert state.operation=='invalidate' and state.generation==2
    reconcile_page(session_factory=factory)
    db.refresh(state)
    assert state.generation==2  # Repeated sweeps do not manufacture retries.


def test_one_oversized_source_cannot_starve_later_expiry(knowledge,monkeypatch):
    from datetime import datetime,timedelta
    from contextlib import contextmanager
    from app.modules.talents import graph_reconcile
    from app.modules.talents.graph_models import GraphSource
    from app.core.errors import BizError
    db,_,first,_=published(knowledge)
    _,_,second,_=published(knowledge)
    second.expires_at=datetime.now()-timedelta(seconds=1);db.commit()
    original=graph_reconcile.source_projection
    def one_oversized(db,job):
        if job.source_id==first.id:raise BizError(422,'投影片段过多')
        return original(db,job)
    monkeypatch.setattr(graph_reconcile,'source_projection',one_oversized)
    @contextmanager
    def factory():yield db
    graph_reconcile.reconcile_page(session_factory=factory)
    rows=list(db.scalars(select(GraphSource).order_by(GraphSource.id)))
    assert [row.operation for row in rows]==['invalidate','invalidate']
