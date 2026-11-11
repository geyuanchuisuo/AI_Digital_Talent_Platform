"""Opt-in SQL/API/outbox integration; Neo4j transport is explicitly a test double."""
import os
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark=pytest.mark.skipif(os.getenv('B09_GRAPH_MYSQL')!='1' or
    not os.getenv('MYSQL_DB','').startswith('ai_talent_b09_graph_'),reason='opt-in dedicated B09 graph database required')


def test_graph_shape_concurrent_claim_recovery_and_old_lease_on_mysql(monkeypatch):
    """An owned throwaway table prevents unrelated producers affecting claims."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import MetaData
    from sqlalchemy.orm import registry
    from app.database import SessionLocal,engine
    from app.modules.talents.graph_models import GraphSyncJob
    from app.tasks import runner as module
    from app.config import settings
    assert settings.mysql_db.startswith('ai_talent_b09_graph_')
    table=GraphSyncJob.__table__.to_metadata(MetaData(),name='b09_graph_claim_'+uuid4().hex[:12])
    mapper=registry()
    class Probe:pass
    mapper.map_imperatively(Probe,table)
    table.create(engine,checkfirst=False)
    kind=module._Kind('graph_sync',Probe,'图谱并发隔离验证',fenced=True)
    monkeypatch.setattr(module,'KINDS',(kind,))
    try:
        with SessionLocal() as db:
            row=Probe(source_kind='job',source_id=1,source_version=1,generation=1,operation='invalidate',max_attempts=2)
            db.add(row);db.commit();task_id=row.id
        first,second=module.TaskRunner(),module.TaskRunner()
        barrier=Barrier(2)
        def claim(runner):
            barrier.wait(timeout=5)
            return runner._claim(kind,1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(claim,[first,second]))
        assert sum(len(outcome) for outcome in outcomes)==1
        old=(outcomes[0] or outcomes[1])[0][1]
        second._startup_recovery()
        claimed=second._claim(kind,1)
        assert claimed[0][0]==task_id and claimed[0][1]!=old
        first._mark_failed(kind,task_id,RuntimeError('late'),old)
        with SessionLocal() as db:
            row=db.get(Probe,task_id)
            assert row.status=='running' and row.attempt_count==2
        second._startup_recovery()
        assert second._claim(kind,1)==[]
        with SessionLocal() as db:assert db.get(Probe,task_id).status=='failed'
    finally:
        table.drop(engine,checkfirst=False)
        mapper.dispose()


def test_confirmation_delivery_revocation_and_delete_with_real_sql(monkeypatch):
    from app.api.deps import get_current_user
    from app.api.talents import router
    from app.core.errors import register_exception_handlers
    from app.database import SessionLocal
    from app.models import User,TalentProfile,ResumeFile,ParsedResume
    from app.models.resume import ResumePage
    from app.modules.talents.history import save_version
    from app.modules.talents.graph_models import GraphSource,GraphSyncJob
    from app.modules.talents.graph_worker import handle_graph_sync
    from app.modules.talents.graph_outbox import invalidate_existing
    from app.config import settings
    from app.tasks.runner import TaskRunner,KINDS
    monkeypatch.setattr(settings,'neo4j_enabled',True)
    tag=uuid4().hex[:12]
    with SessionLocal() as db:
        person=TalentProfile(name='合成同步档案',skills_json=['Python'])
        admin=User(username='b09_sync_admin_'+tag,password_hash='synthetic-disabled-login',role='hr')
        db.add_all([person,admin]);db.flush()
        reader=User(username='b09_sync_reader_'+tag,password_hash='synthetic-disabled-login',role='personal',person_id=person.id)
        db.add(reader);db.flush()
        file=ResumeFile(filename='synthetic-sync.pdf',stored_path='synthetic/unused.pdf',file_hash=uuid4().hex*2,
            file_ext='pdf',file_size=10,upload_batch_id=uuid4().hex[:26],uploaded_by=admin.id)
        db.add(file);db.flush()
        parsed=ParsedResume(resume_file_id=file.id,talent_profile_id=person.id,raw_text='使用Python。',structured_json={'skills':['Python']})
        db.add_all([parsed,ResumePage(resume_file_id=file.id,page_no=1,text_content=parsed.raw_text,quality_status='ready')])
        db.flush();save_version(db,person,'baseline');db.commit()
        pid,aid,rid=person.id,admin.id,reader.id
        db.expunge(admin);db.expunge(reader)
    actor=[admin]
    app=FastAPI();register_exception_handlers(app);app.include_router(router)
    app.dependency_overrides[get_current_user]=lambda:actor[0]
    api=TestClient(app)
    class Transport:
        def __init__(self):self.calls=[]
        def replace(self,*args):self.calls.append(args);return True
        def read_projection(self,*args):
            projection=self.calls[-1][-1]
            return dict(nodes=[{k:n[k] for k in ('key','kind','label')} for n in projection['nodes']],
                edges=[{k:e[k] for k in ('key','from','to','type','evidence','review_status','ability_verified')} for e in projection['edges']])
    transport=Transport()
    monkeypatch.setattr('app.modules.talents.graph_read.get_client',lambda:transport)
    def execute(task_id):
        with SessionLocal() as db:
            job=db.get(GraphSyncJob,task_id)
            job.status='running';job.lease_token='test-lease';job.attempt_count+=1;db.commit()
        handle_graph_sync(task_id,'test-lease',session_factory=SessionLocal,client=transport)
    try:
        actor[0]=reader
        assert api.post(f'/api/talents/{pid}/graph-confirm',json={'version':1}).status_code==403
        actor[0]=admin
        assert api.post(f'/api/talents/{pid}/graph-confirm',json={'version':2}).status_code==409
        response=api.post(f'/api/talents/{pid}/graph-confirm',json={'version':1})
        assert response.status_code==200,response.text
        task_id=response.json()['data']['task_id']
        execute(task_id)
        actor[0]=reader
        response=api.get(f'/api/talents/{pid}/graph')
        assert response.status_code==200,response.text
        assert response.json()['data']['neo4j_state']=='synced' and response.headers['Cache-Control']=='no-store'
        with SessionLocal() as db:
            db.get(User,aid).is_active=0;db.commit()
            # Explicit new source generation exercises submitter reauthorization.
            from app.modules.talents.graph_outbox import enqueue
            job=enqueue(db,'profile',pid,1,operation='upsert',requested_by=aid,force=True)
            db.commit();revoked_id=job.id
        writes=len(transport.calls)
        execute(revoked_id)
        assert len(transport.calls)==writes
        with SessionLocal() as db:
            state=db.scalar(select(GraphSource).where(GraphSource.source_kind=='profile',GraphSource.source_id==pid))
            assert state.operation=='invalidate'
            removal=db.scalar(select(GraphSyncJob).where(GraphSyncJob.source_kind=='profile',GraphSyncJob.source_id==pid,
                GraphSyncJob.generation==state.generation))
            removal_id=removal.id
        execute(removal_id)
        assert transport.calls[-1][-1] is None  # Purge works after submitter is disabled.
        with SessionLocal() as db:
            db.get(User,rid).is_active=0;db.commit()
        assert api.get(f'/api/talents/{pid}/graph').status_code==403
    finally:
        with SessionLocal() as db:
            for uid in (aid,rid):db.get(User,uid).is_active=0
            db.commit()
