from contextlib import contextmanager
import pytest
from app.core.errors import BizError
from test_knowledge_service import knowledge
from test_round1_versions import db


def _published(db, service, *, scope='public', text='合成制度规定申请需审批。'):
    from app.modules.knowledge.models import KnowledgeChunk
    doc = service.create_document(db, '合成制度', scope, user_id=7)
    revision = service.upload(db, doc.id, 'policy.txt', text.encode(), user_id=7)
    revision.status, revision.stage, revision.index_token = 'completed', 'completed', 'synthetic-index'
    chunk = KnowledgeChunk(revision_id=revision.id, chunk_no=0, page_no=None, source_type='txt',
        offset_start=0, offset_end=len(text), text=text)
    db.add(chunk)
    db.commit()
    service.publish(db, doc.id, revision.id, user_id=7)
    context = dict(chunk_id=chunk.id, document_id=doc.id, revision_id=revision.id, version=revision.version,
        document_title=doc.title, filename=revision.filename, page_no=None, source_type='txt',
        offset_start=0, offset_end=len(text), snippet=text, index_token=revision.index_token)
    return doc, revision, context


def _run(db, job_id):
    from app.modules.knowledge.models import KnowledgeQuestionJob
    from app.modules.knowledge.qa_worker import handle_question
    row = db.get(KnowledgeQuestionJob, job_id)
    row.status, row.lease_token, row.attempt_count = 'running', 'question-lease', 1
    db.commit()
    @contextmanager
    def factory():
        yield db
    handle_question(job_id, 'question-lease', session_factory=factory)


def test_no_authorized_evidence_refuses_without_calling_model(knowledge, monkeypatch):
    db, service = knowledge
    from app.modules.knowledge import questions, qa_worker
    monkeypatch.setattr(qa_worker, 'generate', lambda *args: pytest.fail('no evidence must not call model'))
    job = questions.create(db, '请假规定？', 'empty', user_id=8)
    _run(db, job['job_id'])
    value = questions.read(db, job['job_id'], user_id=8)
    assert value['status'] == 'completed' and value['result']['status'] == 'insufficient_evidence'


def test_public_question_uses_only_authorized_versions_and_valid_citations(knowledge, monkeypatch):
    db, service = knowledge
    from app.modules.knowledge import questions, qa_worker, index
    doc, revision, context = _published(db, service)
    _published(db, service, scope='admin', text='受限内部制度原文')
    def search(db, question, revisions, *, authorize=None):
        revisions = authorize() if authorize else revisions
        assert [r.id for r in revisions] == [revision.id]
        return [context]
    def generate(question, candidates, calls):
        assert [c['snippet'] for c in candidates] == [context['snippet']]
        assert 'index_token' not in candidates[0]
        calls.append({'model': 'synthetic-provider', 'status': 'success'})
        return {'answer': '需要审批。', 'citation_ids': [1]}
    monkeypatch.setattr(index, 'search', search)
    monkeypatch.setattr(qa_worker, 'generate', generate)
    job = questions.create(db, '请假规定？', 'public', user_id=8)
    assert questions.create(db, '请假规定？', 'public', user_id=8)['job_id'] == job['job_id']
    _run(db, job['job_id'])
    value = questions.read(db, job['job_id'], user_id=8)
    assert value['result']['answer'] == '需要审批。'
    assert value['result']['citations'][0]['revision_id'] == revision.id
    service.update_document(db, doc.id, user_id=7, enabled=False)
    hidden = questions.read(db, job['job_id'], user_id=8)
    assert hidden['status'] == 'source_unavailable' and hidden['result'] is None
    with pytest.raises(BizError) as exc:
        questions.read(db, job['job_id'], user_id=7)
    assert exc.value.code == 403


def test_deletion_during_model_call_cannot_publish_answer(knowledge, monkeypatch):
    db, service = knowledge
    from app.modules.knowledge import questions, qa_worker, index
    doc, revision, context = _published(db, service)
    monkeypatch.setattr(index, 'search', lambda *args, **kwargs: [context])
    def generate(*args):
        service.delete_document(db, doc.id, user_id=7)
        return {'answer': '迟到回答', 'citation_ids': [1]}
    monkeypatch.setattr(qa_worker, 'generate', generate)
    job = questions.create(db, '请假规定？', 'delete', user_id=8)
    _run(db, job['job_id'])
    result = questions.read(db, job['job_id'], user_id=8)
    assert result['status'] == 'failed' and result['result'] is None


def test_forged_citation_and_key_conflict_are_rejected(knowledge, monkeypatch):
    db, service = knowledge
    from app.modules.knowledge import questions, qa_worker
    questions.create(db, '问题一', 'same', user_id=8)
    with pytest.raises(BizError) as exc:
        questions.create(db, '问题二', 'same', user_id=8)
    assert exc.value.code == 409
    monkeypatch.setattr(qa_worker, 'generate', lambda *args: {'answer': '合成回答', 'citation_ids': [2]})
    with pytest.raises(BizError):
        qa_worker.answer('问题', [{'snippet': '合成制度原文'}], [])
