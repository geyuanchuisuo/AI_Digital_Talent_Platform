"""Source-page upserts and durable parse-attempt outcomes; no model calls."""
from datetime import datetime
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import delete, func, select

from app.models.resume import ParseAttempt, ParseTask, ResumePage

_attempt_id = ContextVar("parse_attempt_id", default=None)


@contextmanager
def attempt_scope(attempt_id):
    token = _attempt_id.set(attempt_id)
    try:
        yield
    finally:
        _attempt_id.reset(token)


def _open_attempt(db, task_id):
    query = select(ParseAttempt).where(ParseAttempt.parse_task_id == task_id, ParseAttempt.status == "running")
    if _attempt_id.get() is not None:
        query = query.where(ParseAttempt.id == _attempt_id.get())
    return db.scalar(query.order_by(ParseAttempt.attempt_no.desc()).limit(1))


def store_pages(db, file_id, pages):
    if not pages:
        return
    existing = {row.page_no: row for row in db.scalars(
        select(ResumePage).where(ResumePage.resume_file_id == file_id))}
    numbers = set()
    for page in pages:
        numbers.add(page.page_no)
        row = existing.get(page.page_no)
        if row is None:
            row = ResumePage(resume_file_id=file_id, page_no=page.page_no)
            db.add(row)
        row.text_content = page.text
        row.source_type = page.source_type
        row.ocr_engine = "rapidocr-onnxruntime" if page.source_type == "ocr" else None
        row.confidence = page.confidence
        row.quality_status = page.quality_status
        row.error_message = page.error_message
    for number, row in existing.items():
        if number not in numbers:
            db.delete(row)
    db.flush()


def begin_attempt(db, task):
    from app.config import settings
    db.execute(select(ParseTask.id).where(ParseTask.id == task.id).with_for_update())
    for unfinished in db.scalars(select(ParseAttempt).where(ParseAttempt.parse_task_id == task.id,
                                                            ParseAttempt.status == "running")):
        unfinished.status = "failed"
        unfinished.error_message = "上次执行中断，任务已重新认领"
        unfinished.finished_at = datetime.now()
    number = db.scalar(select(func.max(ParseAttempt.attempt_no)).where(ParseAttempt.parse_task_id == task.id)) or 0
    row = ParseAttempt(parse_task_id=task.id, attempt_no=number + 1, status="running",
                       stage=task.stage or "extract", started_at=datetime.now(),
                       trace_json={"configured_model":settings.llm_model,"prompt_version":settings.prompt_version})
    db.add(row)
    db.commit()
    return row


def trace_attempt(db, task_id, **values):
    attempt = _open_attempt(db, task_id)
    if attempt is None:
        return
    if "parse_input" in values:
        from app.core.prompts.loader import get_prompt
        prompt = get_prompt("parse")
        values.update(prompt_version=prompt.version, parse_system_prompt=prompt.system,
                      parse_date=datetime.now().date().isoformat())
    attempt.trace_json = {**(attempt.trace_json or {}), **values}
    db.flush()


def append_model_call(db, task_id, event):
    attempt = _open_attempt(db, task_id)
    if attempt is None:
        return
    trace = dict(attempt.trace_json or {})
    calls = list(trace.get("model_calls", []))
    if len(calls) < 16:
        calls.append(event)
    else:
        trace["model_calls_truncated"] = True
    trace["model_calls"] = calls
    attempt.trace_json = trace
    db.commit()


def finish_attempt(db, attempt, *, status, stage, error=None):
    db.refresh(attempt)
    if attempt.status != "running":
        return  # A recovered attempt's failure must not be overwritten by a late worker.
    attempt.status = status
    attempt.stage = stage
    attempt.error_message = error[:2000] if error else None
    attempt.finished_at = datetime.now()
    db.commit()
