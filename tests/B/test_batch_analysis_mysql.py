"""Opt-in isolated SQL/API/worker validation; no model or vector calls."""
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

pytestmark = pytest.mark.skipif(os.getenv('B08_ANALYSIS_MYSQL') != '1' or
    not os.getenv('MYSQL_DB', '').startswith('ai_talent_b08_analysis_'),
    reason='opt-in dedicated B08 analysis database required')


def test_statistics_freezes_legacy_profile_versions_and_recovers_without_model(monkeypatch):
    from app.database import SessionLocal
    from app.models import User, TalentProfile
    from app.models.talent import ProfileQuestionJob
    from app.modules.system.models import SysRole, SysUserRole
    from app.modules.talents.history import save_version
    from app.modules.talents.question_worker import handle_question_job
    from app.api.deps import get_current_user
    from app.api.talents import router
    from app.core.errors import register_exception_handlers
    tag = uuid4().hex[:12]
    with SessionLocal() as db:
        user = User(username='b08_analysis_'+tag, password_hash='synthetic-disabled-login', role='hr', data_scope_mode='scoped')
        # Effective management codes use the canonical hr role, not an invented role.
        role = db.scalar(select(SysRole).where(SysRole.code=='hr'))
        if role is None:
            role = SysRole(code='hr', name='Synthetic HR', data_scope='all')
            db.add(role);db.flush()
        people = [TalentProfile(name='合成同名', skills_json=['Java'], work_years=0),
                  TalentProfile(name='合成同名', skills_json=['JavaScript'], work_years=5)]
        db.add_all([user,*people]);db.flush()
        binding = SysUserRole(user_id=user.id,role_id=role.id)
        db.add(binding);db.commit()
        uid, pids, bid = user.id, [p.id for p in people], binding.id
        db.expunge(user)
    app=FastAPI();register_exception_handlers(app);app.include_router(router)
    app.dependency_overrides[get_current_user]=lambda:user
    client=TestClient(app)
    monkeypatch.setattr('app.modules.talents.qa_service.ask',lambda *a,**k:pytest.fail('statistics called QA'))
    monkeypatch.setattr('app.modules.talents.qa_service.retrieve',lambda *a,**k:pytest.fail('statistics called retrieval'))
    payload={'person_ids':pids,'mode':'statistics','question':'统计档案字段','idempotency_key':tag,
             'criteria':[{'field':'skills','operator':'contains','value':'Java'}]}
    try:
        response=client.post('/api/talents/question-jobs',json=payload)
        assert response.status_code==200,response.text
        created=response.json()['data'];jid=created['job_id']
        assert [t['version'] for t in created['targets']]==[1,1]
        with SessionLocal() as db:
            first=db.get(TalentProfile,pids[0]);first.skills_json=['Go']
            save_version(db,first,'manual_edit',changed_by=uid)
            # Simulate a worker resuming an already-claimed, persisted request.
            db.execute(update(ProfileQuestionJob).where(ProfileQuestionJob.id==jid).values(
                status='running',lease_token=tag,attempt_count=2));db.commit()
        handle_question_job(jid,tag)
        result=client.get(f'/api/talents/question-jobs/{jid}').json()['data']
        assert result['status']=='completed' and result['processed']==2
        assert result['analysis']['matched_ids']==[pids[0]]
        assert result['analysis']['not_matched_ids']==[pids[1]]
        assert result['analysis']['rows'][0]['fields']['skills']['value']==['Java']
        restored=client.get('/api/talents/question-jobs',params=[('person_ids',p) for p in pids]+[('mode','statistics')])
        assert restored.json()['data']['job_id']==jid
        assert restored.json()['data']['criteria'][0]['value']=='java'
        assert client.post('/api/talents/question-jobs',json=payload).json()['data']['job_id']==jid
        assert client.post('/api/talents/question-jobs',json={**payload,'idempotency_key':tag+'x',
            'criteria':[{'field':'phone','operator':'eq','value':'1'}]}).status_code==422
        with SessionLocal() as db:
            db.delete(db.get(SysUserRole,bid));db.commit()
        assert client.get(f'/api/talents/question-jobs/{jid}').status_code==403
    finally:
        with SessionLocal() as db:
            db.get(User,uid).is_active=0;db.commit()
