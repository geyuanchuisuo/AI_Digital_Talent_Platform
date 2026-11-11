"""Real isolated MySQL/API/worker roundtrip, retrieval substituted explicitly."""
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from app.api.deps import get_current_user, get_account_scope_dep
from app.api.talents import router
from app.core.auth.scope import get_account_scope
from app.core.errors import register_exception_handlers
from app.database import SessionLocal
from app.models.talent import ProfileQuestion, ProfileQuestionJob, TalentProfile, TalentProfileEdit, TalentProfileVersion
from app.models.user import User
from app.modules.talents.history import save_version
from app.modules.talents.question_worker import handle_question_job


@pytest.mark.skipif(not os.getenv('MYSQL_DB', '').startswith('ai_talent_'), reason='requires explicitly selected isolated MySQL database')
def test_api_worker_and_durable_result_roundtrip(monkeypatch):
    tag = uuid4().hex
    uid = None
    pids = []
    jid = None
    try:
        with SessionLocal() as db:
            user = User(username=f'qa_job_{tag[:16]}', password_hash='synthetic-test-only', role='hr')
            db.add(user)
            people = [TalentProfile(name=f'Synthetic QA {tag[:8]}-{i}') for i in range(2)]
            db.add_all(people)
            db.flush()
            for person in people:
                save_version(db, person, 'baseline')
            db.commit()
            uid, pids = user.id, [p.id for p in people]
            scope = get_account_scope(user)
            db.expunge(user)
        application = FastAPI()
        register_exception_handlers(application)
        application.include_router(router)
        application.dependency_overrides[get_current_user] = lambda: user
        application.dependency_overrides[get_account_scope_dep] = lambda: scope
        # No model or Milvus request: prove durable insufficient-evidence publication.
        monkeypatch.setattr('app.modules.talents.qa_service.retrieve', lambda *args: [])
        client = TestClient(application)
        payload = {'person_ids': pids, 'question': '合成测试技能？', 'idempotency_key': tag}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post('/api/talents/question-jobs', json=payload), range(2)))
        assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
        assert len({r.json()['data']['job_id'] for r in responses}) == 1
        response = responses[0]
        assert response.status_code == 200, response.text
        created = response.json()['data']
        jid = created['job_id']
        assert created['status'] == 'pending'
        assert [t['version'] for t in created['targets']] == [1, 1]
        with SessionLocal() as db:
            db.execute(update(ProfileQuestionJob).where(ProfileQuestionJob.id == jid).values(
                status='running', lease_token=tag, attempt_count=1))
            db.commit()
        handle_question_job(jid, tag)
        response = client.get(f'/api/talents/question-jobs/{jid}')
        assert response.status_code == 200, response.text
        result = response.json()['data']
        assert result['status'] == 'completed' and result['processed'] == 2
        assert result['answered'] == 0
        assert all(item['status'] == 'insufficient_evidence' for item in result['items'])
        assert client.post('/api/talents/question-jobs', json=payload).json()['data']['job_id'] == jid
        restored = client.get('/api/talents/question-jobs', params=[('person_ids', p) for p in pids])
        assert restored.json()['data']['job_id'] == jid
        assert client.post(f'/api/talents/question-jobs/{jid}/cancel').json()['data']['status'] == 'completed'
    finally:
        with SessionLocal() as db:
            if uid is not None:
                db.execute(delete(ProfileQuestion).where(ProfileQuestion.asked_by == uid))
                db.execute(delete(ProfileQuestionJob).where(ProfileQuestionJob.asked_by == uid))
            if pids:
                db.execute(delete(TalentProfileEdit).where(TalentProfileEdit.talent_profile_id.in_(pids)))
                db.execute(delete(TalentProfileVersion).where(TalentProfileVersion.talent_profile_id.in_(pids)))
                db.execute(delete(TalentProfile).where(TalentProfile.id.in_(pids)))
            if uid is not None:
                db.execute(delete(User).where(User.id == uid))
            db.commit()
