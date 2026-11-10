from datetime import datetime

import pytest
from sqlalchemy import select, func

from test_round1_versions import db
from app.models import resume, talent
from app.service.extract_service import ExtractedPage
from app.modules.talents.history import save_version, read_version


def bundle(db, ext="pdf"):
    profile = talent.TalentProfile(name="张晓林", city="人工确认城市", skills_json=["Python", "Erlang"],
                                   manual_edited_fields=["city"])
    source = resume.ResumeFile(filename="合成样例." + ext, stored_path="synthetic/unused." + ext,
        file_hash="a" * 64, file_ext=ext, file_size=10, upload_batch_id="A" * 26, uploaded_by=1)
    db.add_all([profile, source]); db.flush()
    parsed = resume.ParsedResume(resume_file_id=source.id, talent_profile_id=profile.id,
        structured_json={"basic":{"name":"张晓林", "city":"北京"}, "skills":["Python", "Erlang"]},
        raw_text="张晓林 北京 Python", llm_model="unit-test")
    task = resume.ParseTask(resume_file_id=source.id, status="running")
    db.add_all([parsed, task]); db.commit()
    return profile, source, task


def test_source_pages_store_quality_and_update_without_duplicates(db):
    from app.modules.files.processing import store_pages
    _, source, _ = bundle(db)
    page = ExtractedPage(1, "张晓林 北京 Python", "ocr", 0.7, "low_confidence")
    store_pages(db, source.id, [page]); store_pages(db, source.id, [page]); db.commit()
    rows = db.scalars(select(resume.ResumePage)).all()
    assert len(rows) == 1
    assert rows[0].source_type == "ocr"
    assert rows[0].quality_status == "low_confidence"


def test_each_retry_preserves_a_separate_attempt_outcome(db):
    from app.modules.files.processing import begin_attempt, finish_attempt
    _, _, task = bundle(db)
    first = begin_attempt(db, task)
    finish_attempt(db, first, status="failed", stage="extract", error="第 2 页 OCR 超时")
    second = begin_attempt(db, task)
    finish_attempt(db, second, status="success", stage=None)
    rows = db.scalars(select(resume.ParseAttempt).order_by(resume.ParseAttempt.attempt_no)).all()
    assert [(r.attempt_no, r.status) for r in rows] == [(1,"failed"), (2,"success")]
    assert rows[0].error_message == "第 2 页 OCR 超时"
    assert all(r.finished_at is not None for r in rows)


def test_new_version_automatically_collects_literal_source_evidence_only(db):
    from app.modules.files.processing import store_pages
    profile, source, _ = bundle(db)
    store_pages(db, source.id, [ExtractedPage(1, "张晓林 北京 Python", "ocr", 0.7, "low_confidence")])
    version = save_version(db, profile, "baseline"); db.commit()
    evidence = read_version(db, profile.id, version)["evidence"]
    assert any(e["field_path"] == "basic.name" and "张晓林" in e["snippet"] for e in evidence)
    assert any(e["field_path"] == "skills" and "Python" in e["snippet"] for e in evidence)
    assert not any(e["field_path"] == "basic.city" for e in evidence)
    assert all("Erlang" not in e["snippet"] for e in evidence)
    assert all(e["source"] == "ocr" and e["review_status"] == "pending_review" for e in evidence)
    before = len(evidence)
    assert save_version(db, profile, "reparse") == version
    db.commit()
    assert len(read_version(db, profile.id, version)["evidence"]) == before


def test_docx_evidence_is_document_text_not_a_fabricated_physical_page(db):
    from app.modules.files.processing import store_pages
    profile, source, _ = bundle(db, "docx")
    store_pages(db, source.id, [ExtractedPage(1, "张晓林 Python", "document")])
    version = save_version(db, profile, "baseline"); db.commit()
    item = read_version(db, profile.id, version)["evidence"][0]
    assert item["page_no"] is None
    assert item["locator_type"] == "document_text"


def test_failed_extraction_preserves_pages_before_a_model_is_called(db, monkeypatch):
    from app.tasks import parse_worker
    from app.service.extract_service import ExtractError
    profile, source, task = bundle(db)
    pages = [ExtractedPage(1, "张晓林 Python"),
             ExtractedPage(2, "", "ocr", quality_status="failed", error_message="OCR 超时")]
    def fail(*args, **kwargs):
        raise ExtractError("第 2 页 OCR 超时", pages=pages)
    called = []
    monkeypatch.setattr(parse_worker, "extract_text", fail)
    monkeypatch.setattr(parse_worker.parse_service, "extract_structured", lambda *a: called.append(True))
    with pytest.raises(parse_worker.ParseError, match="OCR 超时"):
        parse_worker._run_extract(db, task, source, None)
    assert not called
    stored = db.scalars(select(resume.ResumePage).order_by(resume.ResumePage.page_no)).all()
    assert len(stored) == 2 and stored[1].quality_status == "failed"


def test_attempt_keeps_its_own_input_output_and_prompt_snapshot(db):
    from app.modules.files.processing import begin_attempt, finish_attempt, trace_attempt
    _, _, task = bundle(db)
    first = begin_attempt(db, task)
    trace_attempt(db, task.id, parse_input="合成输入", parse_output={"basic":{"name":"张晓林"}})
    finish_attempt(db, first, status="success", stage=None)
    second = begin_attempt(db, task)
    assert first.trace_json['parse_input'] == "合成输入"
    assert first.trace_json['parse_output']['basic']['name'] == "张晓林"
    assert first.trace_json['prompt_version'] and first.trace_json['parse_system_prompt']
    assert 'parse_input' not in second.trace_json


def test_new_claim_closes_an_interrupted_running_attempt(db):
    from app.modules.files.processing import begin_attempt, finish_attempt, attempt_scope, trace_attempt
    _, _, task=bundle(db)
    first=begin_attempt(db,task)
    # A killed process never reached finish_attempt; the recovered task is claimed again.
    second=begin_attempt(db,task)
    with attempt_scope(first.id):
        trace_attempt(db, task.id, parse_input='late result from an old execution')
    assert 'parse_input' not in second.trace_json
    finish_attempt(db,second,status='success',stage=None)
    db.refresh(first)
    assert first.status=='failed' and first.finished_at is not None
    assert '中断' in first.error_message


def test_failed_semantic_model_outputs_survive_worker_failure_rollback(db, monkeypatch):
    from app.tasks import parse_worker
    from app.service.extract_service import ExtractResult
    from app.schemas.parse import ResumeSchema
    _, _, task=bundle(db)
    empty=ResumeSchema.model_validate({'basic':{'name':'合成无经历'}})
    monkeypatch.setattr(parse_worker,'SessionLocal',lambda:db)
    monkeypatch.setattr(parse_worker,'extract_text',lambda *a,**kw:ExtractResult('只有姓名',False,4))
    monkeypatch.setattr(parse_worker.parse_service.llm_client,'chat_json',lambda *a,**kw:empty)
    parse_worker.handle_parse(task.id)
    attempt=db.scalar(select(resume.ParseAttempt).where(resume.ParseAttempt.parse_task_id==task.id))
    assert attempt.status=='failed'
    calls=attempt.trace_json['model_calls']
    assert len(calls)==2 and all(call['output']['employments']==[] for call in calls)
