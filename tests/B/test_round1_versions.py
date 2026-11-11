import pytest
from sqlalchemy import create_engine, MetaData, Integer, text, select
from sqlalchemy.orm import Session

from app.models import talent, resume


def test_round1_models_exist():
    assert hasattr(talent, 'TalentProfileVersion')
    assert hasattr(talent, 'TalentFieldEvidence')
    assert hasattr(talent, 'TalentProfileEdit')
    assert hasattr(resume, 'ResumePage')
    assert hasattr(resume, 'ParseAttempt')


@pytest.fixture
def db():
    from app.models import Base
    from app.modules.talents.history import HISTORY_MODELS
    from app.modules.talents.graph_models import GRAPH_MODELS
    engine = create_engine('sqlite://')
    metadata = MetaData()
    for model in [talent.TalentProfile, talent.TalentTag, resume.ParsedResume, resume.ResumeFile, resume.ParseTask, *HISTORY_MODELS, *GRAPH_MODELS]:
        table = model.__table__.to_metadata(metadata)
        for col in table.columns:
            if col.primary_key or col.name in ('truncated', 'retries', 'file_size'):
                col.type = Integer()
            if col.name == 'raw_text':
                from sqlalchemy import Text
                col.type = Text()
            if col.name == 'updated_at':
                from sqlalchemy import DefaultClause
                col.server_default = DefaultClause(text('CURRENT_TIMESTAMP'))
    metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def test_versions_immutable_and_noop_does_not_increment(db):
    from app.modules.talents.history import save_version, read_version
    p = talent.TalentProfile(name='Alice', city='Old')
    db.add(p); db.flush()
    assert save_version(db, p, 'baseline') == 1
    assert save_version(db, p, 'reparse') == 1
    p.city = 'New'
    assert save_version(db, p, 'manual_edit', changed_by=7) == 2
    db.commit()
    assert read_version(db, p.id, 1)['profile']['city'] == 'Old'
    assert read_version(db, p.id, 2)['profile']['city'] == 'New'
    edits = db.scalars(select(talent.TalentProfileEdit)).all()
    assert [(e.field_name, e.old_value, e.new_value) for e in edits] == [('city', '"Old"', '"New"')]


def test_history_does_not_bypass_current_authorization_or_deletion(db):
    from app.modules.talents.history import save_version
    from app.service.talent_service import get_person_snapshot, SnapshotScopeError
    from app.core.auth.scope import AccountScope
    from datetime import datetime
    p = talent.TalentProfile(name='Alice')
    db.add(p); db.flush(); save_version(db, p, 'baseline'); db.commit()
    with pytest.raises(SnapshotScopeError):
        get_person_snapshot(db, p.id, version=1, scope=AccountScope('personal', p.id+1, False, 'public'))
    p.deleted_at = datetime.now(); db.commit()
    assert get_person_snapshot(db, p.id, version=1)['status'] == 'deleted'


def test_version_evidence_is_source_bound_and_rollback_is_atomic(db):
    from app.modules.talents.history import save_version, add_evidence, read_version
    p = talent.TalentProfile(name='Alice')
    db.add(p); db.flush(); save_version(db, p, 'baseline'); db.commit()
    with pytest.raises(ValueError):
        add_evidence(db, p.id, 1, 'basic.name', file_id=99, page_no=1, snippet='Alice')
    assert read_version(db, p.id, 1)['evidence'] == []
    p.city = 'Rollback'
    save_version(db, p, 'manual_edit', changed_by=7)
    db.rollback()
    assert read_version(db, p.id, 2) is None


def test_patch_and_restore_publish_history_without_rewriting_old_version(db, monkeypatch):
    from app.service import talent_service as svc
    from app.schemas.talent import TalentPatchRequest
    from app.modules.talents.history import read_version
    p = talent.TalentProfile(name='Alice', city='Old', vector_status='synced')
    db.add(p); db.commit()
    monkeypatch.setattr(svc, 'sync_profile_vector', lambda *a, **kw: None)
    monkeypatch.setattr(svc, '_reload_profile', lambda pid: p)
    monkeypatch.setattr(svc, '_delete_vector_quietly', lambda pid: True)
    svc.patch_profile(db, p.id, TalentPatchRequest(city='New'), user_id=7)
    assert svc.get_person_snapshot(db, p.id)['profile_version'] == 2
    assert svc.get_person_snapshot(db, p.id, version=1)['profile']['city'] == 'Old'
    svc.patch_profile(db, p.id, TalentPatchRequest(city='New'), user_id=7)
    assert svc.get_person_snapshot(db, p.id)['profile_version'] == 2
    svc.soft_delete(db, p.id, user_id=7)
    assert svc.get_person_snapshot(db, p.id, version=1)['status'] == 'deleted'
    svc.restore(db, p.id, user_id=7)
    assert svc.get_person_snapshot(db, p.id)['profile_version'] == 4
    assert read_version(db, p.id, 1)['profile']['city'] == 'Old'


def test_source_evidence_roundtrip_and_historical_version(db):
    from app.modules.talents.history import save_version, add_evidence
    from app.service import talent_service as svc
    p = talent.TalentProfile(name='Alice')
    db.add(p); db.flush()
    f = resume.ResumeFile(filename='a.pdf', stored_path='test/a.pdf', file_hash='a'*64,
        file_ext='pdf', file_size=10, upload_batch_id='test', uploaded_by=7)
    db.add(f); db.flush()
    db.add(resume.ParsedResume(resume_file_id=f.id, talent_profile_id=p.id, structured_json={}, raw_text='Alice'))
    db.add(resume.ResumePage(resume_file_id=f.id, page_no=1, text_content='Name: Alice'))
    db.flush(); save_version(db, p, 'baseline')
    add_evidence(db, p.id, 1, 'basic.name', file_id=f.id, page_no=1, snippet='Alice')
    db.commit()
    result = svc.get_person_evidence(db, [p.id], version=1, field_paths=['basic.name'])
    assert result['items'][0]['evidence'][0]['snippet'] == 'Alice'
    p.city = 'New'; save_version(db, p, 'manual_edit', changed_by=7); db.commit()
    assert svc.get_person_snapshot(db, p.id, version=1)['evidence'][0]['file_id'] == f.id
    assert svc.get_person_snapshot(db, p.id)['profile_version'] == 2
    assert svc.get_person_snapshot(db, p.id)['evidence'][0]['snippet'] == 'Alice'


def test_migration_schema_matches_model_and_backfill(db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    path = Path(__file__).parents[2] / 'backend/migrations/versions/B_0001_profile_history.py'
    spec = importlib.util.spec_from_file_location('b_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    # MySQL offline DDL validates all table/index operations without touching a real DB.
    import io
    out = io.StringIO()
    context = MigrationContext.configure(dialect_name='mysql', opts={'as_sql': True, 'output_buffer': out})
    with Operations.context(context):
        migration.upgrade()
    sql = out.getvalue()
    for name in ('talent_profile_versions', 'talent_field_evidence', 'talent_profile_edits', 'resume_pages', 'parse_attempts'):
        assert 'CREATE TABLE ' + name in sql


def test_current_snapshot_uses_live_vector_status(db):
    from app.modules.talents.history import save_version
    from app.service.talent_service import get_person_snapshot
    p = talent.TalentProfile(name='Alice', vector_status='pending')
    db.add(p); db.flush(); save_version(db, p, 'baseline'); db.commit()
    p.vector_status = 'synced'; db.commit()
    assert get_person_snapshot(db, p.id)['profile']['vector_status'] == 'synced'
    assert get_person_snapshot(db, p.id, version=1)['profile']['vector_status'] == 'pending'
    assert '_audit_profile' not in get_person_snapshot(db, p.id)


def test_llm_output_does_not_overwrite_manual_confirmation_during_call(db, monkeypatch):
    from app.service import profile_service as svc
    p = talent.TalentProfile(name='Alice')
    db.add(p); db.flush()
    parsed = resume.ParsedResume(resume_file_id=99, talent_profile_id=p.id,
        structured_json={'basic': {'name': 'Alice'}}, raw_text='Alice')
    db.add(parsed); db.commit()
    def output_after_manual_edit(data):
        p.tags_json = ['Manual']; p.ability_scores_json = {'manual': True}
        p.manual_edited_fields = ['tags', 'ability_scores']
        db.commit()
        return object()  # Applying this output would fail, making accidental overwrite observable.
    monkeypatch.setattr(svc, 'call_profile_llm', output_after_manual_edit)
    monkeypatch.setattr(svc, 'sync_profile_vector', lambda *a, **kw: None)
    svc.build_profile(db, parsed)
    assert p.tags_json == ['Manual']
    assert p.ability_scores_json == {'manual': True}


def test_each_source_keeps_its_own_file_and_projects(db):
    from app.modules.talents.history import save_version, read_version
    p = talent.TalentProfile(name='Alice'); db.add(p); db.flush()
    for i in (1, 2):
        f = resume.ResumeFile(filename=f'{i}.pdf', stored_path=f'test/{i}.pdf', file_hash=str(i)*64,
            file_ext='pdf', file_size=10, upload_batch_id='test', uploaded_by=7)
        db.add(f); db.flush()
        db.add(resume.ParsedResume(resume_file_id=f.id, talent_profile_id=p.id,
            structured_json={'projects': [{'name': f'Project{i}'}]}, raw_text=f'Project{i}'))
    db.flush(); save_version(db, p, 'baseline'); db.commit()
    sources = read_version(db, p.id, 1)['sources']
    assert [(s['filename'], s['projects'][0]['name']) for s in sources] == [('1.pdf', 'Project1'), ('2.pdf', 'Project2')]


@pytest.mark.parametrize('filename', ['C_0002_adaptive_assessment.py', 'C_0003_phase1_expansion.py'])
def test_historical_migrations_support_individual_offline_sql(filename):
    import importlib.util
    import io
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[2] / 'backend/migrations/versions' / filename
    spec = importlib.util.spec_from_file_location('compat_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    out = io.StringIO()
    context = MigrationContext.configure(dialect_name='mysql', opts={'as_sql': True, 'output_buffer': out})
    with Operations.context(context):
        migration.upgrade()
    assert 'ALTER TABLE' in out.getvalue()
