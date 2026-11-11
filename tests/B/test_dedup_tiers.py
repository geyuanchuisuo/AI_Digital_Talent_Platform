"""B04 identity tiers: same-name is never identity, automatic merges stay reversible."""
from types import SimpleNamespace as N

import pytest
from sqlalchemy import Integer, MetaData, select

from test_round1_versions import db
from app.models import TalentDuplicate, TalentProfile, ParsedResume, ResumeFile, ParseTask
from app.models.talent import TalentProfileEdit
from app.service import dedup_service as svc, profile_service
from app.modules.talents.history import read_version


@pytest.fixture(autouse=True)
def duplicates(db):
    metadata = MetaData()
    table = TalentDuplicate.__table__.to_metadata(metadata)
    table.c.id.type = Integer()
    metadata.create_all(db.get_bind())


def source(db, **basic):
    values = dict(name='合成人甲', phone='13800000000', email='synthetic@example.test')
    values.update(basic)
    file = ResumeFile(filename='synthetic.txt', stored_path='unused', file_hash='c'*64,
                      file_ext='txt', file_size=10, upload_batch_id='D'*26, uploaded_by=7)
    db.add(file); db.flush()
    parsed = ParsedResume(resume_file_id=file.id, structured_json={'basic': values, 'skills': ['Go']},
                          raw_text='合成人甲 13800000000 synthetic@example.test')
    task = ParseTask(resume_file_id=file.id, status='running', stage='profile')
    db.add_all([parsed, task]); db.commit()
    return parsed, task


def target(db, **kw):
    values = dict(name='合成人甲', phone='13800000000', email='synthetic@example.test', city='原城市')
    values.update(kw)
    p = TalentProfile(**values)
    db.add(p); db.commit()
    return p


def test_unique_three_identity_signals_are_high(db):
    p = target(db)
    parsed, _ = source(db)
    match = svc.detect(db, parsed)
    assert match.profile_id == p.id
    assert match.confidence == 'high'
    assert match.matched_fields == ('name', 'phone', 'email')
    assert match.candidate_count == 1


def test_original_phone_adjacent_to_numeric_email_is_verified_separately(db):
    target(db, email='123abc@example.test')
    parsed, _ = source(db, email='123abc@example.test')
    parsed.raw_text = '合成人甲 +86 138-0000-0000 123abc@example.test'
    assert svc.detect(db, parsed).confidence == 'high'


def test_placeholder_name_is_never_automatic_identity(db):
    target(db, name='未知')
    parsed, _ = source(db, name='未知')
    parsed.raw_text = '未知 13800000000 synthetic@example.test'
    assert svc.detect(db, parsed).confidence == 'medium'


@pytest.mark.parametrize('changes,conflict', [
    ({'email': 'different@example.test'}, 'email'),
    ({'phone': '13900000000'}, 'phone'),
    ({'name': '合成人乙'}, 'name'),
    ({'birth_year': 1990}, 'birth_year'),
    ({'gender': '女'}, 'gender'),
])
def test_identity_conflicts_stay_manual(db, changes, conflict):
    target(db, birth_year=1991, gender='男')
    parsed, _ = source(db, birth_year=1991, gender='男', **{
        k: v for k, v in changes.items() if k not in ('birth_year', 'gender')})
    if conflict in ('birth_year', 'gender'):
        data = dict(parsed.structured_json)
        data['basic'] = dict(data['basic'], **changes)
        parsed.structured_json = data
    match = svc.detect(db, parsed)
    assert match.confidence == 'medium'
    assert conflict in match.conflict_fields


def test_phone_and_email_candidate_union_blocks_automatic(db):
    first = target(db)
    target(db, phone='13900000000')  # shared mailbox, not a unique person
    parsed, _ = source(db)
    match = svc.detect(db, parsed)
    assert match.profile_id == first.id
    assert match.confidence == 'medium' and match.candidate_count == 2


def test_name_only_never_matches(db):
    target(db)
    parsed, _ = source(db, phone=None, email=None)
    assert svc.detect(db, parsed) is None


@pytest.mark.parametrize('raw', ['合成人甲', '合成人甲 13800000000 wrong@example.test'])
def test_model_only_identity_without_original_evidence_stays_manual(db, raw):
    target(db)
    parsed, _ = source(db)
    parsed.raw_text = raw
    match = svc.detect(db, parsed)
    assert match.confidence == 'medium'
    assert 'source_unverified' in match.block_reasons


def test_auto_merge_pipeline_preserves_history_and_undo_is_not_auto_repeated(db, monkeypatch):
    from app.tasks.parse_worker import _run_dedup
    p = target(db, manual_edited_fields=['city'])
    parsed, task = source(db, city='新城市')
    monkeypatch.setattr(svc, '_automatic_actor', lambda *a: 7)
    monkeypatch.setattr(profile_service, 'sync_profile_vector', lambda *a, **k: None)
    assert _run_dedup(db, task, parsed)
    duplicate = db.scalar(select(TalentDuplicate))
    assert duplicate.status == 'merged' and task.status == 'success'
    assert parsed.talent_profile_id == p.id and p.city == '原城市'
    assert read_version(db, p.id, 2)['profile']['city'] == '原城市'
    items, _ = svc.list_duplicates(db, status='merged', offset=0, limit=10)
    assert items[0]['detection']['automatic'] is True
    assert items[0]['detection']['policy_version'] == 'identity-v1'
    svc.undo_merge(db, duplicate.id, user_id=7, reason='核实为不同人')
    assert parsed.talent_profile_id is None
    task.status = 'running'; db.commit()
    assert _run_dedup(db, task, parsed)
    assert duplicate.status == 'pending' and task.status == 'duplicate_pending'


@pytest.mark.parametrize('condition', ['revoked', 'truncated', 'existing_person', 'low_quality'])
def test_auto_merge_rechecks_execution_blockers(db, monkeypatch, condition):
    p = target(db)
    parsed, task = source(db)
    row = svc.flag(db, parsed, svc.detect(db, parsed))
    monkeypatch.setattr(svc, '_automatic_actor', lambda *a: None if condition == 'revoked' else 7)
    monkeypatch.setattr(profile_service, 'sync_profile_vector', lambda *a, **k: pytest.fail('must not sync'))
    if condition == 'truncated': task.truncated = 1
    if condition == 'existing_person': parsed.talent_profile_id = p.id
    if condition == 'low_quality':
        from app.models.resume import ResumePage
        db.add(ResumePage(resume_file_id=parsed.resume_file_id, page_no=1,
                         text_content=parsed.raw_text, source_type='ocr', quality_status='low_confidence'))
    db.commit()
    assert svc.try_auto_merge(db, row.id) is None
    assert row.status == 'pending'


def test_auto_revalidates_identity_changed_after_flag(db, monkeypatch):
    p = target(db)
    parsed, _ = source(db)
    row = svc.flag(db, parsed, svc.detect(db, parsed))
    p.email = 'changed@example.test'; db.commit()
    monkeypatch.setattr(svc, '_automatic_actor', lambda *a: 7)
    assert svc.try_auto_merge(db, row.id) is None
    assert row.status == 'pending' and parsed.talent_profile_id is None


def test_undo_suppression_survives_candidate_reassignment(db, monkeypatch):
    from app.tasks.parse_worker import _run_dedup
    first = target(db)
    parsed, task = source(db)
    monkeypatch.setattr(svc, '_automatic_actor', lambda *a: 7)
    monkeypatch.setattr(profile_service, 'sync_profile_vector', lambda *a, **k: None)
    _run_dedup(db, task, parsed)
    row = db.scalar(select(TalentDuplicate))
    svc.undo_merge(db, row.id, user_id=7, reason='合成身份冲突')
    first.phone = '13900000000'; first.email = 'changed@example.test'; db.commit()
    second = target(db)
    task.status = 'running'; db.commit()
    _run_dedup(db, task, parsed)
    assert row.profile_a_id == second.id
    assert row.status == 'pending' and parsed.talent_profile_id is None


@pytest.mark.parametrize('active,all_people,management,expected', [
    (True, True, True, 7), (False, True, True, None),
    (True, False, True, None), (True, True, False, None),
])
def test_automatic_actor_requires_current_full_management(monkeypatch, active, all_people, management, expected):
    from app.core.auth import scope
    user = N(id=7, is_active=active)
    db = N(get=lambda *a: user)
    monkeypatch.setattr(scope, 'get_account_scope', lambda u: N(can_view_all_persons=all_people))
    monkeypatch.setattr(scope, 'account_is_management', lambda *a: management)
    assert svc._automatic_actor(db, 7) == expected
