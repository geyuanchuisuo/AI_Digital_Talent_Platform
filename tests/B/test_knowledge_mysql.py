"""Real isolated MySQL/API/task lifecycle; vector and model boundaries substituted."""
import os
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select, update
from app.api.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.database import SessionLocal
from app.models.user import User
from app.modules.knowledge.models import KnowledgeDocument, KnowledgeRevision, KnowledgeChunk, KnowledgeQuestionJob
from app.modules.knowledge.router import router
from app.modules.knowledge import service, index, qa_worker
from app.modules.knowledge.ingest import handle_ingest


@pytest.mark.skipif(not os.getenv('MYSQL_DB','').startswith('ai_talent_'), reason='requires explicitly selected isolated MySQL database')
def test_institutional_knowledge_lifecycle_and_authorization(tmp_path, monkeypatch):
    marker = uuid4().hex
    users, docs, revisions = [], [], []
    actor = {}
    def current():
        with SessionLocal() as db:
            user = db.get(User, actor['id'])
            db.expunge(user)
            return user
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = current
    client = TestClient(app)
    monkeypatch.setattr(service, 'storage_dir', lambda: tmp_path)
    monkeypatch.setattr(index, 'upsert', lambda *args, **kwargs: None)
    removed, model_calls = [], []
    monkeypatch.setattr(index, 'remove_revision', lambda rid: removed.append(rid))
    try:
        with SessionLocal() as db:
            admin = User(username='b07_admin_'+marker[:12], password_hash='synthetic', role='hr')
            reader = User(username='b07_read_'+marker[:12], password_hash='synthetic', role='personal')
            db.add_all([admin, reader]);db.commit()
            admin_id, reader_id = admin.id, reader.id
            users = [admin_id, reader_id]
        actor['id'] = admin_id
        for scope in ('public','admin'):
            response = client.post('/api/knowledge/documents', json={'title':'合成制度 '+scope,'access_scope':scope})
            assert response.status_code == 200, response.text
            did = response.json()['data']['id'];docs.append(did)
            payload = '合成请假制度：请假需提前审批。' if scope=='public' else '合成受限内部制度，不得向普通用户展示。'
            response = client.post(f'/api/knowledge/documents/{did}/versions', files={'file':('policy.txt',payload.encode(),'text/plain')})
            assert response.status_code == 200, response.text
            rid = response.json()['data']['id'];revisions.append(rid)
            repeated = client.post(f'/api/knowledge/documents/{did}/versions', files={'file':('policy.txt',payload.encode(),'text/plain')})
            assert repeated.json()['data']['id'] == rid
            assert client.post(f'/api/knowledge/documents/{did}/publish',json={'revision_id':rid}).status_code == 409
            token = uuid4().hex
            with SessionLocal() as db:
                db.execute(update(KnowledgeRevision).where(KnowledgeRevision.id==rid).values(status='running',lease_token=token,attempt_count=1));db.commit()
            handle_ingest(rid,token)
            assert client.post(f'/api/knowledge/documents/{did}/publish',json={'revision_id':rid}).status_code == 200
        actor['id'] = reader_id
        visible = client.get('/api/knowledge/documents').json()['data']
        assert visible['total'] == 1 and visible['items'][0]['id'] == docs[0]
        assert not visible['can_manage']
        assert client.get(f'/api/knowledge/versions/{revisions[1]}/source').status_code == 403
        assert client.post('/api/knowledge/documents',json={'title':'越权','access_scope':'public'}).status_code == 403
        assert client.get(f'/api/knowledge/versions/{revisions[0]}/source').status_code == 200
        def search(db, question, allowed, *, authorize=None):
            allowed = authorize() if authorize else allowed
            assert [r.id for r in allowed] == [revisions[0]]
            chunk = db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.revision_id==revisions[0]))
            return [{'chunk_id':chunk.id,'document_id':docs[0],'revision_id':revisions[0],'version':1,
                'document_title':'合成制度 public','filename':'policy.txt','page_no':None,'source_type':'txt',
                'offset_start':chunk.offset_start,'offset_end':chunk.offset_end,'snippet':chunk.text}]
        monkeypatch.setattr(index,'search',search)
        def generate(question, context, calls):
            assert all('受限内部' not in c['snippet'] for c in context)
            model_calls.append(context)
            calls.append({'model':'synthetic-model','status':'success'})
            return {'answer':'请假需提前审批。','citation_ids':[1]}
        monkeypatch.setattr(qa_worker,'generate',generate)
        payload={'question':'请假怎么申请？','idempotency_key':marker}
        response=client.post('/api/knowledge/questions',json=payload)
        assert response.status_code==200,response.text
        jid=response.json()['data']['job_id']
        token=uuid4().hex
        with SessionLocal() as db:
            db.execute(update(KnowledgeQuestionJob).where(KnowledgeQuestionJob.id==jid).values(status='running',lease_token=token,attempt_count=1));db.commit()
        qa_worker.handle_question(jid,token)
        answer=client.get(f'/api/knowledge/questions/{jid}').json()['data']
        assert answer['status']=='completed' and answer['result']['citations'][0]['revision_id']==revisions[0]
        assert len(model_calls)==1 and 'audit_json' not in answer
        assert client.post('/api/knowledge/questions',json=payload).json()['data']['job_id']==jid
        actor['id']=admin_id
        assert client.get(f'/api/knowledge/questions/{jid}').status_code==403
        assert client.patch(f'/api/knowledge/documents/{docs[0]}',json={'expires_at':'2000-01-01T00:00:00'}).status_code==200
        actor['id']=reader_id
        hidden=client.get(f'/api/knowledge/questions/{jid}').json()['data']
        assert hidden['status']=='source_unavailable' and hidden['result'] is None
        assert client.get(f'/api/knowledge/versions/{revisions[0]}/source').status_code==403
        actor['id']=admin_id
        for did,rid in zip(docs,revisions):
            assert client.delete(f'/api/knowledge/documents/{did}').status_code==200
            token=uuid4().hex
            with SessionLocal() as db:
                db.execute(update(KnowledgeRevision).where(KnowledgeRevision.id==rid).values(status='running',lease_token=token,attempt_count=1));db.commit()
            handle_ingest(rid,token)
            with SessionLocal() as db:
                assert db.get(KnowledgeRevision,rid).stage=='deleted'
        assert set(removed)==set(revisions)
        actor['id']=reader_id
        assert client.get('/api/knowledge/documents').json()['data']['items']==[]
    finally:
        with SessionLocal() as db:
            if users:db.execute(delete(KnowledgeQuestionJob).where(KnowledgeQuestionJob.asked_by.in_(users)))
            if revisions:db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.revision_id.in_(revisions)))
            if docs:
                db.execute(delete(KnowledgeRevision).where(KnowledgeRevision.document_id.in_(docs)))
                db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(docs)))
            if users:db.execute(delete(User).where(User.id.in_(users)))
            db.commit()
