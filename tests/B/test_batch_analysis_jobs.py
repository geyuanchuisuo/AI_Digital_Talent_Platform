"""B08 modes reuse the same owner/authorization/version/lease contract."""
from types import SimpleNamespace as N

import pytest
from sqlalchemy import select

from test_question_jobs import jobs, _factory
from test_round1_versions import db
from app.core.errors import BizError
from app.models.talent import ProfileQuestionJob


def setup(jobs, monkeypatch):
    db, svc = jobs
    def frozen(db, pid, version, scope):
        return {'status': 'ready', 'person_id': pid, 'profile_version': version or 3,
            'coverage': 'complete', 'sources': [], 'profile': {'name': '同名', 'skills': ['Java'] if pid == 1 else ['JavaScript']}}
    monkeypatch.setattr(svc, 'snapshot', frozen)
    monkeypatch.setattr(svc, 'analysis_snapshot', frozen, raising=False)
    monkeypatch.setattr(svc, 'current_scope', lambda *a: N(role='hr'))
    return db, svc


CONDITION = [{'field': 'skills', 'operator': 'contains', 'value': 'java'}]


def make(jobs, monkeypatch, mode='statistics', key='analysis-key'):
    db, svc = setup(jobs, monkeypatch)
    created = svc.create(db, [1, 2], '记录统计', mode=mode, criteria=CONDITION,
                         user_id=7, scope=N(role='hr'), key=key)
    row = db.get(ProfileQuestionJob, created['job_id'])
    row.status, row.lease_token, row.attempt_count = 'running', 'analysis-token', 1
    db.commit()
    return db, svc, row.id


def test_statistics_reads_every_fixed_version_without_any_qa_or_retrieval(jobs, monkeypatch):
    from app.modules.talents.question_worker import handle_question_job
    db, svc, jid = make(jobs, monkeypatch)
    monkeypatch.setattr('app.modules.talents.qa_service.ask', lambda *a, **k: pytest.fail('statistics must not call QA'))
    handle_question_job(jid, 'analysis-token', session_factory=_factory(db))
    result = svc.read(db, jid, user_id=7)
    assert result['status'] == 'completed' and result['processed'] == 2
    assert result['mode'] == 'statistics' and result['analysis']['matched_ids'] == [1]
    assert result['analysis']['not_matched_ids'] == [2] and result['analysis']['exact']
    assert result['targets'] == [{'person_id': 1, 'version': 3}, {'person_id': 2, 'version': 3}]


def test_comparison_preserves_distinct_people_and_per_person_citations(jobs, monkeypatch):
    from app.modules.talents.question_worker import handle_question_job
    db, svc, jid = make(jobs, monkeypatch, mode='compare')
    monkeypatch.setattr('app.modules.talents.qa_service.ask', lambda db, pid, *a, **k:
        {'person_id': pid, 'profile_version': 3, 'status': 'insufficient_evidence', 'citations': []})
    handle_question_job(jid, 'analysis-token', session_factory=_factory(db))
    result = svc.read(db, jid, user_id=7)
    assert result['status'] == 'completed' and len(result['items']) == 2
    assert [r['person_id'] for r in result['analysis']['rows']] == [1, 2]
    assert result['analysis']['rows'][0]['fields']['skills']['value'] == ['Java']


def test_modes_and_conditions_are_part_of_idempotency_and_recent_mode_isolated(jobs, monkeypatch):
    db, svc = setup(jobs, monkeypatch)
    first = svc.create(db, [1, 2], '记录统计', user_id=7, scope=N(role='hr'), key='one',
        mode='statistics', criteria=CONDITION)
    with pytest.raises(BizError) as error:
        svc.create(db, [1, 2], '记录统计', user_id=7, scope=N(role='hr'), key='one', mode='compare', criteria=CONDITION)
    assert error.value.code == 409
    with pytest.raises(BizError):
        svc.create(db, [1, 2], '记录统计', user_id=7, scope=N(role='hr'), key='one',
            mode='statistics', criteria=[{'field': 'city', 'operator': 'eq', 'value': '北京'}])
    assert svc.recent(db, user_id=7, person_ids=[1, 2]) is None
    assert svc.recent(db, user_id=7, person_ids=[1, 2], mode='statistics')['job_id'] == first['job_id']


def test_current_revocation_blocks_statistics_result_and_stale_lease_cannot_publish(jobs, monkeypatch):
    from app.modules.talents.question_worker import handle_question_job
    db, svc, jid = make(jobs, monkeypatch)
    svc.cancel(db, jid, user_id=7)
    handle_question_job(jid, 'analysis-token', session_factory=_factory(db))
    row = db.get(ProfileQuestionJob, jid)
    assert row.status == 'cancelled' and 'analysis' not in row.progress_json
    monkeypatch.setattr(svc, 'current_scope', lambda *a: N(role='personal'))
    with pytest.raises(BizError) as error:
        svc.read(db, jid, user_id=7)
    assert error.value.code == 403


@pytest.mark.parametrize('change', ['cancel', 'revoke'])
def test_state_change_during_field_check_cannot_publish_analysis(jobs, monkeypatch, change):
    from app.modules.talents import batch_analysis
    from app.modules.talents.question_worker import handle_question_job
    db, svc, jid = make(jobs, monkeypatch)
    build = batch_analysis.build_analysis
    def interrupted(*args):
        result = build(*args)
        if change == 'cancel':
            svc.cancel(db, jid, user_id=7)
        else:
            monkeypatch.setattr(svc, 'current_scope', lambda *a: N(role='personal'))
        return result
    monkeypatch.setattr(batch_analysis, 'build_analysis', interrupted)
    handle_question_job(jid, 'analysis-token', session_factory=_factory(db))
    db.expire_all()
    row = db.get(ProfileQuestionJob, jid)
    assert row.status == ('cancelled' if change == 'cancel' else 'failed')
    assert row.progress_json == {'items': []}


def test_pre_mode_individual_job_hash_still_replays(jobs, monkeypatch):
    from hashlib import sha256
    import json
    db, svc = setup(jobs, monkeypatch)
    row = ProfileQuestionJob(asked_by=7, idempotency_key='legacy',
        request_hash=sha256(json.dumps([[1], '旧问题', None, None], ensure_ascii=False).encode()).hexdigest(),
        request_json={'question': '旧问题', 'targets': [{'person_id': 1, 'version': 3}], 'follow_up_to': None},
        progress_json={'items': []})
    db.add(row);db.commit()
    restored = svc.create(db, [1], '旧问题', user_id=7, scope=N(role='hr'), key='legacy')
    assert restored['job_id'] == row.id and restored['mode'] == 'individual'


@pytest.mark.parametrize('mode,criteria,ids', [('statistics', [], [1]), ('compare', [], [1]),
    ('individual', CONDITION, [1]), ('sql', [], [1])])
def test_invalid_mode_combinations_rejected(jobs, monkeypatch, mode, criteria, ids):
    db, svc = setup(jobs, monkeypatch)
    with pytest.raises(BizError) as error:
        svc.create(db, ids, '问题', user_id=7, scope=N(role='hr'), key='bad', mode=mode, criteria=criteria)
    assert error.value.code == 422
