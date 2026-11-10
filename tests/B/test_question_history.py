import pytest
from app.core.auth.scope import AccountScope
from app.core.errors import BizError
from app.modules.talents import qa_service
from app.models.talent import ProfileQuestion
from test_round1_versions import db
from test_profile_qa_service import prepare


def records(db):
    profile = prepare(db)
    for owner in [7, 8, 7]:
        db.add(ProfileQuestion(person_id=profile.id, profile_version=1, asked_by=owner,
            status='answered', snapshot_json={'question': 'Question ' + str(owner),
                'system_prompt': 'private', 'model_calls': ['private raw'],
                'result': {'status': 'answered', 'answer': 'Python', 'citations': []}}))
    db.commit()
    return profile, AccountScope('personal', profile.id, False, 'public')


def test_history_is_owner_scoped_paginated_and_never_exposes_model_audit(db):
    profile, scope = records(db)
    result = qa_service.question_history(db, profile.id, user_id=7, scope=scope, offset=0, limit=1)
    assert result['total'] == 2 and len(result['items']) == 1
    item = result['items'][0]
    assert item['question_id'] == 3 and item['question'] == 'Question 7'
    assert item['profile_version'] == 1 and item['answer'] == 'Python'
    assert 'system_prompt' not in item and 'model_calls' not in item
    second = qa_service.question_history(db, profile.id, user_id=7, scope=scope, offset=1, limit=1)
    assert second['items'][0]['question_id'] == 1


def test_history_denies_other_person_and_deleted_profile(db):
    profile, _ = records(db)
    with pytest.raises(Exception):
        qa_service.question_history(db, profile.id, user_id=7,
            scope=AccountScope('personal', profile.id + 1, False, 'public'))
    from datetime import datetime
    profile.deleted_at = datetime.now(); db.commit()
    with pytest.raises(BizError) as error:
        qa_service.question_history(db, profile.id, user_id=7,
            scope=AccountScope('personal', profile.id, False, 'public'))
    assert error.value.code == 409


def test_history_validates_page_bounds(db):
    profile, scope = records(db)
    for offset, limit in [(-1, 10), (0, 0), (0, 101)]:
        with pytest.raises(BizError) as error:
            qa_service.question_history(db, profile.id, user_id=7, scope=scope, offset=offset, limit=limit)
        assert error.value.code == 422


def test_history_hides_citations_when_source_no_longer_belongs_to_person(db):
    profile, scope = records(db)
    row = db.get(ProfileQuestion, 3)
    row.snapshot_json = {'question': 'Private', 'result': {'status': 'answered',
        'answer': 'Formerly merged private fact', 'citations': [{'file_id': 999, 'snippet': 'Other person'}]}}
    db.commit()
    item = qa_service.question_history(db, profile.id, user_id=7, scope=scope, offset=0, limit=1)['items'][0]
    assert item['status'] == 'source_unavailable'
    assert item['answer'] == '' and item['citations'] == []
