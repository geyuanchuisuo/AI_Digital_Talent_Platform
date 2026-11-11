"""Opt-in dedicated MySQL proof transactions; all documents and users are synthetic."""
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func

pytestmark=pytest.mark.skipif(os.getenv('B06_TAG_MYSQL')!='1' or
    not os.getenv('MYSQL_DB','').startswith('ai_talent_b06_tags_'),reason='opt-in dedicated B06 tag database required')


def test_source_review_api_roundtrip_private_reason_and_revocation(monkeypatch):
    from app.api.deps import get_current_user
    from app.api.talents import router
    from app.core.errors import register_exception_handlers
    from app.database import SessionLocal
    from app.models import User,TalentProfile,ResumeFile,ParsedResume
    from app.models.resume import ResumePage
    from app.models.talent import TalentProfileEdit
    from app.modules.system.models import SysRole,SysUserRole
    from app.service import talent_service
    from app.modules.talents.history import save_version
    tag=uuid4().hex[:12]
    with SessionLocal() as db:
        admin=User(username='b06_admin_'+tag,password_hash='synthetic-disabled-login',role='hr',data_scope_mode='scoped')
        role=SysRole(code='hr',name='Synthetic HR',data_scope='all')
        person=TalentProfile(name='合成标签样例')
        db.add_all([admin,role,person]);db.flush()
        personal=User(username='b06_person_'+tag,password_hash='synthetic-disabled-login',role='personal',person_id=person.id)
        binding=SysUserRole(user_id=admin.id,role_id=role.id)
        file=ResumeFile(filename='synthetic-tag.pdf',stored_path='synthetic/unused.pdf',file_hash=uuid4().hex*2,
            file_ext='pdf',file_size=10,upload_batch_id=uuid4().hex[:26],uploaded_by=admin.id)
        db.add_all([personal,binding,file]);db.flush()
        db.add_all([ParsedResume(resume_file_id=file.id,talent_profile_id=person.id,
            raw_text='计划学习Go。',structured_json={'basic':{'name':'合成标签样例'}}),
            ResumePage(resume_file_id=file.id,page_no=1,text_content='计划学习Go。',source_type='text',quality_status='ready')])
        db.flush();save_version(db,person,'baseline');db.commit()
        uid,puid,pid,bid=admin.id,personal.id,person.id,binding.id
        db.expunge(admin);db.expunge(personal)
    application=FastAPI();register_exception_handlers(application);application.include_router(router)
    application.dependency_overrides[get_current_user]=lambda:admin
    client=TestClient(application)
    monkeypatch.setattr(talent_service,'sync_profile_vector',lambda *a,**k:None)
    try:
        suggestions=client.get(f'/api/talents/{pid}/tag-suggestions').json()['data']
        item=next(r for r in suggestions['items'] if r['name']=='Go')
        proof={'code':item['code'],'candidate_id':item['candidate_id'],'catalog_version':suggestions['catalog_version'],
               'profile_version':suggestions['profile_version'],'reason':''}
        payload={'tags':[{'tag':'Go','tag_type':'skill'}],'tag_proofs':[proof]}
        assert client.patch(f'/api/talents/{pid}',json=payload).status_code==422
        proof['reason']='内部复核：仅登记学习意向'
        assert client.patch(f'/api/talents/{pid}',json=payload).status_code==200
        quality=client.get(f'/api/talents/{pid}/tag-quality').json()['data']
        row=next(r for r in quality['items'] if r['tag']=='go')
        assert row['evidence_status']=='reviewed_source' and row['ability_verified'] is False
        assert row['reason']==proof['reason']
        assert client.patch(f'/api/talents/{pid}',json=payload).status_code==409
        application.dependency_overrides[get_current_user]=lambda:personal
        own=client.get(f'/api/talents/{pid}/tag-quality')
        assert own.status_code==200
        assert '内部复核' not in own.text and 'reviewed_by' not in own.text
        assert client.get(f'/api/talents/{pid+1}/tag-quality').status_code==403
        assert client.patch(f'/api/talents/{pid}',json=payload).status_code==403
        application.dependency_overrides[get_current_user]=lambda:admin
        with SessionLocal() as db:
            parsed=db.scalar(select(ParsedResume).where(ParsedResume.resume_file_id==file.id))
            parsed.talent_profile_id=None;save_version(db,db.get(TalentProfile,pid),'reparse');db.commit()
            parsed.talent_profile_id=pid;save_version(db,db.get(TalentProfile,pid),'reparse');db.commit()
        after=client.get(f'/api/talents/{pid}/tag-quality').json()['data']
        stale=next(r for r in after['items'] if r['tag']=='go')
        assert stale['evidence_status']=='needs_reconfirmation' and stale['evidence']==[]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(TalentProfileEdit).where(
                TalentProfileEdit.talent_profile_id==pid,TalentProfileEdit.field_name=='_tag_review_v1'))==1
            db.delete(db.get(SysUserRole,bid));db.commit()
        assert client.get(f'/api/talents/{pid}/tag-quality').status_code==403
    finally:
        with SessionLocal() as db:
            db.get(User,uid).is_active=0;db.get(User,puid).is_active=0;db.commit()
