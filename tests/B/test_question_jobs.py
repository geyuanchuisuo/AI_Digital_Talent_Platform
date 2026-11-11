from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import DefaultClause, Integer, MetaData, text

from app.core.errors import BizError
from test_round1_versions import db


@pytest.fixture
def jobs(db, monkeypatch):
    from app.models.talent import ProfileQuestion, ProfileQuestionJob
    from app.modules.talents import question_jobs as svc
    metadata = MetaData()
    for model in (ProfileQuestionJob, ProfileQuestion):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if column.primary_key:
                column.type = Integer()
            if column.name == 'updated_at':
                column.server_default = DefaultClause(text('CURRENT_TIMESTAMP'))
    metadata.create_all(db.get_bind())
    monkeypatch.setattr(svc, 'snapshot', lambda db, pid, version, scope: {
        'status': 'ready', 'profile_version': version or 3, 'sources': []})
    monkeypatch.setattr(svc, 'current_scope', lambda db, uid: SimpleNamespace())
    return db, svc


def test_same_request_is_idempotent_but_reusing_key_for_new_question_conflicts(jobs):
    db, svc = jobs
    first = svc.create(db, [1, 2], '技能？', user_id=7, scope=None, key='same-key')
    repeated = svc.create(db, [1, 2], '技能？', user_id=7, scope=None, key='same-key')
    assert repeated['job_id'] == first['job_id']
    assert repeated['targets'] == [{'person_id': 1, 'version': 3}, {'person_id': 2, 'version': 3}]
    with pytest.raises(BizError) as exc:
        svc.create(db, [1, 2], '新问题', user_id=7, scope=None, key='same-key')
    assert exc.value.code == 409


def test_owner_and_current_authorization_are_checked_on_read(jobs, monkeypatch):
    db, svc = jobs
    job = svc.create(db, [1], '技能？', user_id=7, scope=None, key='owner-key')
    with pytest.raises(BizError) as exc:
        svc.read(db, job['job_id'], user_id=8)
    assert exc.value.code == 403
    def denied(*args):
        raise BizError(403, '无权访问该资源')
    monkeypatch.setattr(svc, 'snapshot', denied)
    with pytest.raises(BizError) as exc:
        svc.read(db, job['job_id'], user_id=7)
    assert exc.value.code == 403


def test_cancel_prevents_late_worker_checkpoint(jobs):
    db, svc = jobs
    from app.models.talent import ProfileQuestionJob
    job = svc.create(db, [1], '技能？', user_id=7, scope=None, key='cancel-key')
    row = db.get(ProfileQuestionJob, job['job_id'])
    row.status, row.lease_token = 'running', 'old-token'
    db.commit()
    svc.cancel(db, row.id, user_id=7)
    assert not svc.checkpoint(db, row.id, 'old-token', {'items': [{'answer': 'late'}]})
    db.refresh(row)
    assert row.status == 'cancelled'
    assert row.progress_json == {'items': []}


def _running(jobs, *, progress=None, attempt=1):
    db, svc = jobs
    from app.models.talent import ProfileQuestionJob
    value = svc.create(db, [1, 2], '技能？', user_id=7, scope=None, key='worker-key')
    row = db.get(ProfileQuestionJob, value['job_id'])
    row.status, row.lease_token, row.attempt_count = 'running', 'current-token', attempt
    row.progress_json = progress or {'items': []}
    db.commit()
    return row.id


def _factory(db):
    @contextmanager
    def factory():
        yield db
    return factory


def test_resume_skips_checkpointed_people_and_uses_frozen_versions(jobs, monkeypatch):
    db, svc = jobs
    from app.modules.talents.question_worker import handle_question_job
    jid = _running(jobs, progress={'items': [{'person_id': 1, 'status': 'insufficient_evidence', 'citations': []}]})
    calls = []
    def ask(db, pid, question, **kwargs):
        calls.append((pid, kwargs['version']))
        return {'person_id': pid, 'status': 'insufficient_evidence', 'citations': []}
    monkeypatch.setattr('app.modules.talents.qa_service.ask', ask)
    handle_question_job(jid, 'current-token', session_factory=_factory(db))
    result = svc.read(db, jid, user_id=7)
    assert calls == [(2, 3)]
    assert result['status'] == 'completed' and result['processed'] == 2


def test_cancel_during_provider_call_cannot_publish_answer(jobs, monkeypatch):
    db, svc = jobs
    from app.modules.talents.question_worker import handle_question_job
    jid = _running(jobs)
    def ask(*args, **kwargs):
        svc.cancel(db, jid, user_id=7)
        return {'person_id': 1, 'status': 'answered', 'answer': 'late', 'citations': []}
    monkeypatch.setattr('app.modules.talents.qa_service.ask', ask)
    handle_question_job(jid, 'current-token', session_factory=_factory(db))
    result = svc.read(db, jid, user_id=7)
    assert result['status'] == 'cancelled' and result['items'] == []


@pytest.mark.parametrize('attempt,expected', [(1, 'pending'), (3, 'failed')])
def test_transient_failures_have_finite_retry_budget(jobs, monkeypatch, attempt, expected):
    db, svc = jobs
    from app.modules.talents.question_worker import handle_question_job
    jid = _running(jobs, attempt=attempt)
    def unavailable(*args, **kwargs):
        raise BizError(503, 'synthetic provider unavailable')
    monkeypatch.setattr('app.modules.talents.qa_service.ask', unavailable)
    handle_question_job(jid, 'current-token', session_factory=_factory(db))
    value = svc.read(db, jid, user_id=7)
    assert value['status'] == expected and value['items'] == []
    assert 'synthetic' not in value['error']


def test_revocation_during_call_prevents_checkpoint(jobs, monkeypatch):
    db, svc = jobs
    from app.modules.talents.question_worker import handle_question_job
    jid = _running(jobs)
    def revoked(*args):
        raise BizError(403, '无权访问该资源')
    def ask(*args, **kwargs):
        monkeypatch.setattr(svc, 'snapshot', revoked)
        return {'person_id': 1, 'status': 'answered', 'answer': 'restricted', 'citations': []}
    monkeypatch.setattr('app.modules.talents.qa_service.ask', ask)
    handle_question_job(jid, 'current-token', session_factory=_factory(db))
    from app.models.talent import ProfileQuestionJob
    row = db.get(ProfileQuestionJob, jid)
    assert row.status == 'failed' and row.progress_json['items'] == []


def test_saved_audit_is_reused_after_crash_before_checkpoint(jobs, monkeypatch):
    db, svc = jobs
    from app.models.talent import ProfileQuestion
    from app.modules.talents.question_worker import handle_question_job
    jid = _running(jobs, progress={'items': [{'person_id': 2, 'status': 'insufficient_evidence', 'citations': []}]})
    db.add(ProfileQuestion(person_id=1, profile_version=3, asked_by=7, status='answered', snapshot_json={
        'job_id': jid, 'result': {'status': 'answered', 'answer': 'saved', 'citations': []}}))
    db.commit()
    monkeypatch.setattr('app.modules.talents.qa_service.ask', lambda *a, **kw: pytest.fail('must reuse saved audit'))
    handle_question_job(jid, 'current-token', session_factory=_factory(db))
    value = svc.read(db, jid, user_id=7)
    assert value['status'] == 'completed' and value['answered'] == 1


def test_short_timeout_configuration_still_gets_a_heartbeat_before_expiry(monkeypatch):
    from app.config import settings
    from app.modules.talents.question_worker import _heartbeat_interval
    monkeypatch.setattr(settings, 'task_heartbeat_timeout', 1)
    assert 0 < _heartbeat_interval() < 1


def test_real_snapshot_scope_error_is_a_forbidden_business_response(monkeypatch):
    from app.modules.talents import question_jobs, qa_service
    from app.service.talent_service import SnapshotScopeError
    def forbidden(*args, **kwargs):
        raise SnapshotScopeError('synthetic denied person')
    monkeypatch.setattr(question_jobs, 'get_person_snapshot', forbidden)
    monkeypatch.setattr(qa_service, 'get_person_snapshot', forbidden)
    for function in (lambda: question_jobs.snapshot(None, 2, None, None),
                     lambda: qa_service.ask_batch(None, [2], '问题', user_id=1, scope=None)):
        with pytest.raises(BizError) as exc:
            function()
        assert exc.value.code == 403 and exc.value.message == '无权访问该资源'
