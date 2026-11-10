import json
import pytest
from sqlalchemy import select
from test_round1_versions import db
from test_profile_qa_service import prepare
from app.modules.talents import qa_service, qa
from app.models.talent import ProfileQuestion
from app.core.auth.scope import AccountScope
from app.core.errors import BizError
from app.modules.talents.history import save_version


def setup_parent(db, monkeypatch):
    profile = prepare(db)
    scope = AccountScope('personal', profile.id, False, 'public')
    monkeypatch.setattr(qa_service, 'retrieve', lambda snapshot, query: [dict(row, _score=1.0)
        for row in snapshot['evidence'][:1]])
    from app.core import llm_client
    monkeypatch.setattr(llm_client, 'chat_json', lambda *a, **kw: qa.Answer(answer='第一轮回答', citation_ids=[1]))
    parent = qa_service.ask(db, profile.id, '有哪些技能？', user_id=7, scope=scope)
    return profile, scope, parent


def test_follow_up_pins_version_and_uses_fresh_evidence_with_untrusted_previous_turn(db, monkeypatch):
    profile, scope, parent = setup_parent(db, monkeypatch)
    profile.city = 'New version'; save_version(db, profile, 'manual_edit', changed_by=7); db.commit()
    captured = {}
    from app.core import llm_client
    def generate(payload, **kwargs):
        captured.update(json.loads(payload))
        return qa.Answer(answer='本轮只依据当前原文', citation_ids=[1])
    monkeypatch.setattr(llm_client, 'chat_json', generate)
    answer = qa_service.ask(db, profile.id, '这项技能的依据呢？', user_id=7, scope=scope,
        follow_up_to=parent['question_id'])
    assert answer['profile_version'] == 1 and answer['follow_up_to'] == parent['question_id']
    assert captured['question'] == '这项技能的依据呢？'
    assert captured['previous_turn']['question'] == '有哪些技能？'
    assert captured['previous_turn']['answer'] == '第一轮回答'
    assert captured['evidence'][0]['snippet'] and captured['evidence'][0]['citation_id'] == 1
    audit = db.get(ProfileQuestion, answer['question_id']).snapshot_json
    assert audit['follow_up_to'] == parent['question_id'] and audit['retrieval_query']
    assert '第一轮回答' not in audit['retrieval_query']
    assert audit['prompt_version'] != 'profile-qa-v1'


@pytest.mark.parametrize('owner,person,status,expected',[(8,None,'answered',403),(7,999,'answered',403),
    (7,None,'running',409),(7,None,'failed',409)])
def test_invalid_parent_never_calls_embedding_or_creates_child(db, monkeypatch, owner, person, status, expected):
    profile, scope, parent = setup_parent(db, monkeypatch)
    row = db.get(ProfileQuestion, parent['question_id'])
    row.asked_by = owner; row.person_id = person or profile.id; row.status = status; db.commit()
    monkeypatch.setattr(qa_service, 'retrieve', lambda *a: pytest.fail('must validate parent first'))
    with pytest.raises(BizError) as error:
        qa_service.ask(db, profile.id, '继续？', user_id=7, scope=scope, follow_up_to=parent['question_id'])
    assert error.value.code == expected
    assert len(db.scalars(select(ProfileQuestion)).all()) == 1


def test_missing_parent_and_version_mismatch_are_rejected_without_new_audit(db, monkeypatch):
    profile, scope, parent = setup_parent(db, monkeypatch)
    for parent_id, version, expected in [(999, None, 403),(parent['question_id'], 2, 409)]:
        with pytest.raises(BizError) as error:
            qa_service.ask(db, profile.id, '继续？', user_id=7, scope=scope,
                follow_up_to=parent_id, version=version)
        assert error.value.code == expected
    assert len(db.scalars(select(ProfileQuestion)).all()) == 1


def test_previous_answer_alone_cannot_create_an_answer_when_new_retrieval_is_empty(db, monkeypatch):
    profile, scope, parent = setup_parent(db, monkeypatch)
    monkeypatch.setattr(qa_service, 'retrieve', lambda *a: [])
    from app.core import llm_client
    monkeypatch.setattr(llm_client, 'chat_json', lambda *a, **k: pytest.fail('no evidence means no model'))
    answer = qa_service.ask(db, profile.id, '继续？', user_id=7, scope=scope, follow_up_to=parent['question_id'])
    assert answer['status'] == 'insufficient_evidence' and answer['citations'] == []


def test_parent_version_with_detached_source_is_rejected_before_retrieval(db, monkeypatch):
    profile, scope, parent = setup_parent(db, monkeypatch)
    from app.models.resume import ParsedResume
    row = db.scalar(select(ParsedResume)); row.talent_profile_id = None; db.commit()
    monkeypatch.setattr(qa_service, 'retrieve', lambda *a: pytest.fail('detached source'))
    with pytest.raises(BizError) as error:
        qa_service.ask(db, profile.id, '继续？', user_id=7, scope=scope, follow_up_to=parent['question_id'])
    assert error.value.code == 409
