"""Opt-in real SQL/API evidence previews; no graph or model connection exists."""
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark=pytest.mark.skipif(os.getenv('B09_PREVIEW_MYSQL')!='1' or
    not os.getenv('MYSQL_DB','').startswith('ai_talent_b09_preview_'),reason='opt-in dedicated B09 preview database required')


def test_profile_and_published_policy_preview_auth_versions_and_invalidation():
    from app.api.deps import get_current_user
    from app.api.talents import router
    from app.modules.knowledge.router import router as knowledge_router
    from app.core.errors import register_exception_handlers
    from app.database import SessionLocal
    from app.models import User,TalentProfile,ResumeFile,ParsedResume
    from app.models.resume import ResumePage
    from app.modules.talents.history import save_version
    from app.modules.knowledge import service
    from app.modules.knowledge.models import KnowledgeChunk
    tag=uuid4().hex[:12]
    with SessionLocal() as db:
        person=TalentProfile(name='合成图谱同名',skills_json=['Python'])
        other=TalentProfile(name='合成图谱同名')
        admin=User(username='graph_admin_'+tag,password_hash='synthetic-disabled-login',role='hr')
        db.add_all([person,other,admin]);db.flush()
        reader=User(username='graph_reader_'+tag,password_hash='synthetic-disabled-login',role='personal',person_id=person.id)
        outsider=User(username='graph_outsider_'+tag,password_hash='synthetic-disabled-login',role='personal',person_id=other.id)
        db.add_all([reader,outsider]);db.flush()
        file=ResumeFile(filename='synthetic-graph.pdf',stored_path='synthetic/unused.pdf',file_hash=uuid4().hex*2,
            file_ext='pdf',file_size=10,upload_batch_id=uuid4().hex[:26],uploaded_by=admin.id)
        db.add(file);db.flush()
        parsed=ParsedResume(resume_file_id=file.id,talent_profile_id=person.id,
            raw_text='使用Python，持有PMP。',structured_json={'skills':['Python'],'certificates':['PMP']})
        db.add_all([parsed,ResumePage(resume_file_id=file.id,page_no=1,text_content=parsed.raw_text,quality_status='ready')])
        db.flush();save_version(db,person,'baseline');db.commit()
        pid,parsed_id=person.id,parsed.id
        doc=service.create_document(db,'合成图谱制度','public',user_id=admin.id)
        revision=service.upload(db,doc.id,'graph.txt','使用Python。'.encode(),user_id=admin.id)
        revision.status=revision.stage='completed';revision.index_token='synthetic'
        db.add(KnowledgeChunk(revision_id=revision.id,chunk_no=0,page_no=None,source_type='txt',
            offset_start=0,offset_end=9,text='使用Python。'));db.commit()
        service.publish(db,doc.id,revision.id,user_id=admin.id)
        did,uid,rid,oid=doc.id,admin.id,reader.id,outsider.id
        for user in (admin,reader,outsider):db.expunge(user)
    actor=[reader]
    app=FastAPI();register_exception_handlers(app);app.include_router(router);app.include_router(knowledge_router)
    app.dependency_overrides[get_current_user]=lambda:actor[0]
    client=TestClient(app)
    try:
        response=client.get(f'/api/talents/{pid}/graph-preview')
        assert response.status_code==200,response.text
        assert response.headers['Cache-Control']=='no-store'
        result=response.json()['data']
        assert result['neo4j_state']=='not_connected' and result['edges']
        assert {n['kind'] for n in result['nodes']}=={'Person','Skill','Certificate'}
        actor[0]=outsider
        denied=client.get(f'/api/talents/{pid}/graph-preview')
        assert denied.status_code==403 and 'Python' not in denied.text
        policy=client.get(f'/api/knowledge/documents/{did}/graph-preview')
        assert policy.status_code==200 and policy.headers['Cache-Control']=='no-store'
        with SessionLocal() as db:
            service.update_document(db,did,user_id=uid,access_scope='admin')
            person=db.get(TalentProfile,pid);person.city='新城';save_version(db,person,'manual_edit',changed_by=uid);db.commit()
        assert client.get(f'/api/knowledge/documents/{did}/graph-preview').status_code==403
        actor[0]=reader
        assert client.get(f'/api/talents/{pid}/graph-preview?version=1').json()['data']['fingerprint']==result['fingerprint']
        with SessionLocal() as db:
            db.get(ParsedResume,parsed_id).talent_profile_id=None
            save_version(db,db.get(TalentProfile,pid),'reparse');db.commit()
        unavailable=client.get(f'/api/talents/{pid}/graph-preview?version=1')
        assert unavailable.status_code==409 and 'PMP' not in unavailable.text
    finally:
        with SessionLocal() as db:
            for id in (uid,rid,oid):db.get(User,id).is_active=0
            db.commit()
