from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import DefaultClause, Integer, MetaData, text

from app.core.errors import BizError
from test_round1_versions import db


@pytest.fixture
def knowledge(db, monkeypatch, tmp_path):
    from app.models.user import User
    from app.modules.system.models import SysRole, SysUserRole, SysDepartment
    from app.modules.recruitment.scope_models import UserScopeGrant
    from app.modules.knowledge.models import KNOWLEDGE_MODELS
    from app.modules.knowledge import service
    metadata = MetaData()
    for model in (User, SysRole, SysUserRole, SysDepartment, UserScopeGrant, *KNOWLEDGE_MODELS):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if column.primary_key or column.name == 'is_active':
                column.type = Integer()
            if column.name == 'updated_at':
                column.server_default = DefaultClause(text('CURRENT_TIMESTAMP'))
    metadata.create_all(db.get_bind())
    db.add_all([User(id=7, username='synthetic_knowledge_admin', password_hash='test', role='hr'),
                User(id=8, username='synthetic_knowledge_reader', password_hash='test', role='personal')])
    db.commit()
    @contextmanager
    def factory():
        yield db
    monkeypatch.setattr('app.database.SessionLocal', factory)
    monkeypatch.setattr(service, 'storage_dir', lambda: tmp_path)
    return db, service


def test_only_full_effective_management_can_manage(knowledge):
    db, service = knowledge
    with pytest.raises(BizError) as exc:
        service.create_document(db, '合成制度', 'public', user_id=8)
    assert exc.value.code == 403
    document = service.create_document(db, '合成制度', 'admin', user_id=7)
    assert not service.can_read(db, document, user_id=8)


def test_upload_creates_an_immutable_version_not_a_published_document(knowledge):
    db, service = knowledge
    document = service.create_document(db, '合成制度', 'public', user_id=7)
    first = service.upload(db, document.id, 'policy.txt', '合成制度正文'.encode(), user_id=7)
    second = service.upload(db, document.id, 'policy.txt', '新版制度正文'.encode(), user_id=7)
    assert (first.version, second.version) == (1, 2)
    assert first.file_hash != second.file_hash and first.stored_path != second.stored_path
    assert document.published_revision_id is None
    assert service.allowed_revisions(db, user_id=8) == []
    with pytest.raises(BizError) as exc:
        service.publish(db, document.id, first.id, user_id=7)
    assert exc.value.code == 409


def test_expired_disabled_deleted_documents_are_not_readable(knowledge):
    db, service = knowledge
    document = service.create_document(db, '合成制度', 'public', user_id=7)
    document.expires_at = datetime.now() - timedelta(seconds=1)
    db.commit()
    assert not service.can_read(db, document, user_id=8)
    document.expires_at = None
    document.enabled = False
    db.commit()
    assert not service.can_read(db, document, user_id=8)
    document.enabled = True
    document.deleted_at = datetime.now()
    db.commit()
    assert not service.can_read(db, document, user_id=7)


def _factory(db):
    @contextmanager
    def factory():
        yield db
    return factory


def _running(db, revision, token='synthetic-lease'):
    revision.status, revision.lease_token, revision.attempt_count = 'running', token, 1
    db.commit()


def test_ingest_requires_explicit_publication_and_deletion_stops_recall_first(knowledge, monkeypatch):
    db, service = knowledge
    from app.modules.knowledge.ingest import handle_ingest
    from app.modules.knowledge.models import KnowledgeChunk
    from sqlalchemy import select
    from app.modules.knowledge import index
    monkeypatch.setattr(index, 'upsert', lambda *args, **kwargs: None)
    removed = []
    monkeypatch.setattr(index, 'remove_revision', lambda rid: removed.append(rid))
    doc = service.create_document(db, '合成制度', 'public', user_id=7)
    revision = service.upload(db, doc.id, 'policy.txt', '员工请假应提交申请。'.encode(), user_id=7)
    _running(db, revision)
    handle_ingest(revision.id, 'synthetic-lease', session_factory=_factory(db))
    db.refresh(revision)
    assert revision.status == 'completed' and revision.index_token == 'synthetic-lease'
    chunks = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.revision_id == revision.id)))
    assert chunks[0].text == '员工请假应提交申请。' and chunks[0].page_no is None
    assert service.allowed_revisions(db, user_id=8) == []
    service.publish(db, doc.id, revision.id, user_id=7)
    assert [r.id for r in service.allowed_revisions(db, user_id=8)] == [revision.id]
    service.delete_document(db, doc.id, user_id=7)
    assert service.allowed_revisions(db, user_id=8) == []
    db.refresh(revision)
    assert revision.stage == 'deleting' and revision.status == 'pending'
    _running(db, revision, 'cleanup-lease')
    service.delete_document(db, doc.id, user_id=7)
    db.refresh(revision)
    assert revision.lease_token == 'cleanup-lease'  # repeated deletion is idempotent
    handle_ingest(revision.id, 'cleanup-lease', session_factory=_factory(db))
    db.refresh(revision)
    assert revision.stage == 'deleted' and removed == [revision.id]


def test_restricted_and_stale_vector_hits_never_enter_context(knowledge, monkeypatch):
    db, service = knowledge
    from types import SimpleNamespace
    from app.modules.knowledge import index
    from app.modules.knowledge.models import KnowledgeChunk
    doc = service.create_document(db, '公开合成制度', 'public', user_id=7)
    revision = service.upload(db, doc.id, 'policy.txt', b'synthetic', user_id=7)
    revision.status, revision.stage, revision.index_token = 'completed', 'completed', 'public-token'
    chunk = KnowledgeChunk(revision_id=revision.id, chunk_no=0, page_no=None, source_type='txt',
        offset_start=0, offset_end=9, text='synthetic')
    db.add(chunk)
    db.commit()
    service.publish(db, doc.id, revision.id, user_id=7)
    captured = {}
    def search(**kwargs):
        captured.update(kwargs)
        return [[{'distance': .99, 'entity': {'revision_id': 999, 'chunk_id': 999, 'index_token': 'secret'}},
            {'distance': .99, 'entity': {'revision_id': revision.id, 'chunk_id': chunk.id, 'index_token': 'stale'}},
            {'distance': .9, 'entity': {'revision_id': revision.id, 'chunk_id': chunk.id, 'index_token': 'public-token'}}]]
    monkeypatch.setattr(index, '_client', lambda: SimpleNamespace(has_collection=lambda **kw: True, search=search))
    monkeypatch.setattr('app.core.embedding_client.embed_query', lambda q: [1.0])
    rows = index.search(db, '政策？', service.allowed_revisions(db, user_id=8))
    assert len(rows) == 1 and rows[0]['snippet'] == 'synthetic'
    assert 'public-token' in captured['filter'] and '999' not in captured['filter'] and 'secret' not in captured['filter']


def test_reupload_repairs_missing_source_as_new_version(knowledge):
    from pathlib import Path
    db, service = knowledge
    doc = service.create_document(db, '合成制度', 'admin', user_id=7)
    first = service.upload(db, doc.id, 'policy.txt', b'synthetic', user_id=7)
    assert service.upload(db, doc.id, 'policy.txt', b'synthetic', user_id=7).id == first.id
    Path(first.stored_path).unlink()
    repaired = service.upload(db, doc.id, 'policy.txt', b'synthetic', user_id=7)
    assert repaired.version == 2 and repaired.id != first.id
    assert Path(repaired.stored_path).read_bytes() == b'synthetic'


def test_revocation_during_embedding_is_filtered_before_vector_search(knowledge, monkeypatch):
    from types import SimpleNamespace
    from app.modules.knowledge import index
    db, service = knowledge
    doc = service.create_document(db, '合成制度', 'public', user_id=7)
    revision = service.upload(db, doc.id, 'policy.txt', b'synthetic', user_id=7)
    revision.status, revision.stage, revision.index_token = 'completed', 'completed', 'index-token'
    db.commit()
    service.publish(db, doc.id, revision.id, user_id=7)
    initial = service.allowed_revisions(db, user_id=8)
    def embed(question):
        service.update_document(db, doc.id, user_id=7, access_scope='admin')
        return [1.0]
    monkeypatch.setattr('app.core.embedding_client.embed_query', embed)
    monkeypatch.setattr(index, '_client', lambda: SimpleNamespace(has_collection=lambda **kwargs: True,
        search=lambda **kwargs: pytest.fail('revoked document must not be searched')))
    assert index.search(db, '制度问题', initial, authorize=lambda: service.allowed_revisions(db, user_id=8)) == []


def test_late_upsert_after_cleanup_queues_another_removal_pass(knowledge, monkeypatch):
    from app.modules.knowledge import index
    from app.modules.knowledge.ingest import handle_ingest
    db, service = knowledge
    doc = service.create_document(db, '合成制度', 'public', user_id=7)
    revision = service.upload(db, doc.id, 'policy.txt', b'synthetic', user_id=7)
    _running(db, revision, 'old-indexing-token')
    def late_upsert(*args, **kwargs):
        service.delete_document(db, doc.id, user_id=7)
        db.refresh(revision)
        # The cleanup completed while the old remote vector request was in flight.
        revision.status, revision.stage, revision.lease_token = 'completed', 'deleted', None
        db.commit()
    monkeypatch.setattr(index, 'upsert', late_upsert)
    handle_ingest(revision.id, 'old-indexing-token', session_factory=_factory(db))
    db.refresh(revision)
    assert revision.status == 'pending' and revision.stage == 'deleting'
    assert service.allowed_revisions(db, user_id=8) == []
