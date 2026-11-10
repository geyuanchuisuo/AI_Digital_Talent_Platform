"""A merge must be reversible without erasing later edits or original files."""
import pytest
from sqlalchemy import MetaData, Integer, select

from test_round1_versions import db
from test_round2_processing import bundle
from app.models.talent import TalentDuplicate, TalentTag
from app.models.resume import ResumeFile, ParsedResume, ParseTask
from app.service import dedup_service, profile_service
from app.modules.talents.history import save_version, read_version
from app.core.errors import BizError


def merged(db, monkeypatch):
    metadata = MetaData()
    table = TalentDuplicate.__table__.to_metadata(metadata)
    table.c.id.type = Integer()
    metadata.create_all(db.get_bind())
    master, original, _ = bundle(db)
    master.current_title = 'Original title'
    db.add(TalentTag(talent_profile_id=master.id, tag='Python', tag_type='skill', source='manual'))
    source = ResumeFile(filename='synthetic-new.pdf', stored_path='synthetic/new.pdf', file_hash='b'*64,
                        file_ext='pdf', file_size=10, upload_batch_id='B'*26, uploaded_by=7)
    db.add(source); db.flush()
    parsed = ParsedResume(resume_file_id=source.id, structured_json={
        'basic': {'name': '张晓林', 'current_title': 'New title'}, 'skills': ['Go']}, raw_text='Synthetic Go')
    task = ParseTask(resume_file_id=source.id, status='duplicate_pending', stage='profile')
    db.add_all([parsed, task]); db.flush()
    duplicate = TalentDuplicate(profile_a_id=master.id, parsed_resume_id=parsed.id, reason='phone_match')
    db.add(duplicate); db.commit()
    monkeypatch.setattr(profile_service, 'sync_profile_vector', lambda *a, **k: None)
    dedup_service.resolve(db, duplicate.id, action='merge', user_id=7, note='Confirmed')
    return master, original, source, parsed, task, duplicate


def test_undo_restores_fields_tags_link_and_new_history_without_deleting_files(db, monkeypatch):
    master, original, source, parsed, task, duplicate = merged(db, monkeypatch)
    result = dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='Incorrect identity')
    assert master.current_title == 'Original title'
    assert master.skills_json == ['Python', 'Erlang']
    assert [(t.tag, t.source) for t in db.scalars(select(TalentTag))] == [('Python', 'manual')]
    assert parsed.talent_profile_id is None
    assert duplicate.status == 'pending' and task.status == 'duplicate_pending'
    assert task.profiled_at is None and task.finished_at is None
    assert db.get(ResumeFile, original.id) and db.get(ResumeFile, source.id)
    assert result['profile_version'] == 3
    assert len(read_version(db, master.id, 2)['sources']) == 2
    assert len(read_version(db, master.id, 3)['sources']) == 1
    with pytest.raises(BizError):
        dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='Duplicate undo')


def test_undo_rejects_later_manual_edit_without_overwriting_it(db, monkeypatch):
    master, _, _, parsed, _, duplicate = merged(db, monkeypatch)
    master.city = 'Later edit'
    save_version(db, master, 'manual_edit', changed_by=9); db.commit()
    with pytest.raises(BizError) as error:
        dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='Wrong identity')
    assert error.value.code == 409
    assert master.city == 'Later edit' and parsed.talent_profile_id == master.id


def test_undo_requires_reason_and_never_guesses_legacy_state(db, monkeypatch):
    _, _, _, _, _, duplicate = merged(db, monkeypatch)
    with pytest.raises(BizError) as error:
        dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='  ')
    assert error.value.code == 422
    from app.models.talent import TalentProfileEdit
    from sqlalchemy import delete
    db.execute(delete(TalentProfileEdit)); db.commit()
    with pytest.raises(BizError) as error:
        dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='Wrong identity')
    assert error.value.code == 409


def test_undo_commits_facts_but_marks_failed_vector_for_retry(db, monkeypatch):
    master, _, _, parsed, _, duplicate = merged(db, monkeypatch)
    def unavailable(*args, **kwargs):
        raise RuntimeError('External service unavailable')
    monkeypatch.setattr(profile_service, 'sync_profile_vector', unavailable)
    result = dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='Wrong identity')
    assert result['vector_status'] == 'failed'
    assert master.vector_status == 'failed' and parsed.talent_profile_id is None
    assert duplicate.status == 'pending'


def test_detached_source_is_not_readable_or_questionable_via_old_merged_version(db, monkeypatch):
    master, _, _, _, _, duplicate = merged(db, monkeypatch)
    dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='Wrong identity')
    from app.service.talent_service import get_person_snapshot, get_person_evidence
    from app.modules.talents.qa_service import ask
    from app.core.auth.scope import AccountScope
    result = get_person_snapshot(db, master.id, version=2)
    assert result['status'] == 'source_unavailable'
    assert result['profile'] is None and result.get('source_pages', []) == []
    assert get_person_evidence(db, [master.id], version=2)['items'][0]['evidence'] == []
    with pytest.raises(BizError) as error:
        ask(db, master.id, 'Former source', user_id=8,
            scope=AccountScope('personal', master.id, False, 'public'), version=2)
    assert error.value.code == 409


def test_stale_vector_cannot_match_after_undo_rebuild_failure(db, monkeypatch):
    master, _, _, _, _, duplicate = merged(db, monkeypatch)
    monkeypatch.setattr(profile_service, 'sync_profile_vector', lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    dedup_service.undo_merge(db, duplicate.id, user_id=8, reason='Wrong identity')
    from app.core import embedding_client, milvus_store
    from types import SimpleNamespace as N
    from app.service.talent_service import search_talents
    monkeypatch.setattr(embedding_client, 'embed_query', lambda q: [1.0, 0.0])
    monkeypatch.setattr(milvus_store, 'get_store', lambda: N(search=lambda *a, **k: [N(talent_profile_id=master.id, score=.9)]))
    assert search_talents(db, query='Detached skill', top_k=5) == []


def test_merge_rechecks_duplicate_status_under_profile_lock(db, monkeypatch):
    master, _, _, parsed, _, duplicate = merged(db, monkeypatch)
    from datetime import datetime
    with pytest.raises(BizError) as error:
        dedup_service._merge(db, duplicate, parsed, master.id, 'Second stale request', 9, datetime.now())
    assert error.value.code == 409
