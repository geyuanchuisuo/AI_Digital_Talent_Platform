"""AI 面试间 HTTP 路由（prefix `/api/ai-interviews`）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.errors import BizError
from app.core.llm_client import LLMError
from app.core.response import ok
from app.database import get_db
from app.modules.interviews.ai_judge import default_judge_answer
from app.modules.interviews.ai_models import (
    AiInterviewEvent,
    AiInterviewQuestion,
    AiInterviewSession,
)

router = APIRouter(prefix="/api/ai-interviews", tags=["ai-interviews"])

ADMIN_ROLES = {"admin", "hr"}


class SessionCreate(BaseModel):
    candidate_user_id: int | None = None
    talent_profile_id: int | None = None
    job_id: int | None = None
    application_id: int | None = None


class QuestionCreate(BaseModel):
    question_text: str = Field(min_length=1)
    question_type: str = "structured"


class AnswerSubmit(BaseModel):
    answer_text: str = Field(min_length=1)


def _event(db, session_id, event_type, operator_id=None, note=None):
    db.add(AiInterviewEvent(session_id=session_id, event_type=event_type, operator_id=operator_id, note=note))


@router.post("/sessions")
def create_session(payload: SessionCreate, user: CurrentUser, db: Session = Depends(get_db)):
    if user.role not in ADMIN_ROLES:
        raise BizError(403, "仅管理员可创建 AI 面试")
    session = AiInterviewSession(**payload.model_dump())
    db.add(session)
    db.flush()
    _event(db, session.id, "created", user.id)
    db.commit()
    return ok({"id": session.id})


@router.get("/sessions")
def list_sessions(user: CurrentUser, db: Session = Depends(get_db), candidate_user_id: int | None = None):
    stmt = select(AiInterviewSession).order_by(AiInterviewSession.id.desc())
    if user.role not in ADMIN_ROLES:
        stmt = stmt.where(AiInterviewSession.candidate_user_id == user.id)
    elif candidate_user_id is not None:
        stmt = stmt.where(AiInterviewSession.candidate_user_id == candidate_user_id)
    rows = db.execute(stmt).scalars().all()
    return ok([
        {"id": s.id, "candidate_user_id": s.candidate_user_id, "job_id": s.job_id,
         "application_id": s.application_id, "status": s.status,
         "overall_score": s.overall_score, "started_at": s.started_at, "finished_at": s.finished_at}
        for s in rows
    ])


@router.post("/sessions/{session_id}/start")
def start_session(session_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    session = db.get(AiInterviewSession, session_id)
    if not session:
        raise BizError(404, "面试不存在")
    if session.candidate_user_id and session.candidate_user_id != user.id and user.role not in ADMIN_ROLES:
        raise BizError(403, "无权操作他人面试")
    if session.status != "pending":
        raise BizError(400, "仅 pending 状态可开始")
    session.status = "in_progress"
    session.started_at = datetime.now()
    _event(db, session.id, "started", user.id)
    db.commit()
    return ok({"id": session.id, "status": session.status})


@router.post("/sessions/{session_id}/questions")
def add_question(session_id: int, payload: QuestionCreate, user: CurrentUser, db: Session = Depends(get_db)):
    session = db.get(AiInterviewSession, session_id)
    if not session:
        raise BizError(404, "面试不存在")
    if user.role not in ADMIN_ROLES:
        raise BizError(403, "仅管理员可出题")
    max_seq = db.execute(
        select(AiInterviewQuestion.sequence_no).where(AiInterviewQuestion.session_id == session_id)
    ).scalars().all()
    seq = (max(max_seq) + 1) if max_seq else 1
    question = AiInterviewQuestion(
        session_id=session_id, sequence_no=seq,
        question_text=payload.question_text, question_type=payload.question_type,
    )
    db.add(question)
    db.commit()
    return ok({"id": question.id, "sequence_no": seq})


@router.get("/sessions/{session_id}/questions")
def list_questions(session_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    session = db.get(AiInterviewSession, session_id)
    if not session:
        raise BizError(404, "面试不存在")
    if session.candidate_user_id and session.candidate_user_id != user.id and user.role not in ADMIN_ROLES:
        raise BizError(403, "无权查看")
    rows = db.execute(
        select(AiInterviewQuestion).where(AiInterviewQuestion.session_id == session_id)
        .order_by(AiInterviewQuestion.sequence_no)
    ).scalars().all()
    return ok([
        {"id": q.id, "sequence_no": q.sequence_no, "question_text": q.question_text,
         "question_type": q.question_type, "status": q.status,
         "answer_text": q.answer_text if (user.role in ADMIN_ROLES or q.status != "pending") else None,
         "ai_score": q.ai_score, "ai_feedback": q.ai_feedback_json}
        for q in rows
    ])


@router.post("/questions/{question_id}/answer")
def submit_answer(question_id: int, payload: AnswerSubmit, user: CurrentUser, db: Session = Depends(get_db)):
    question = db.get(AiInterviewQuestion, question_id)
    if not question:
        raise BizError(404, "题目不存在")
    session = db.get(AiInterviewSession, question.session_id)
    if not session or (session.candidate_user_id and session.candidate_user_id != user.id and user.role not in ADMIN_ROLES):
        raise BizError(403, "无权作答")
    if question.status not in ("pending", "failed"):
        raise BizError(400, "该题已评分")
    question.answer_text = payload.answer_text
    try:
        result = default_judge_answer(question.question_text, payload.answer_text)
        question.ai_score = result["score"]
        question.ai_feedback_json = result
        question.status = "graded"
    except LLMError:
        question.status = "failed"
    db.commit()
    return ok({"id": question.id, "status": question.status, "ai_score": question.ai_score})


@router.post("/questions/{question_id}/retry")
def retry_judge(question_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    question = db.get(AiInterviewQuestion, question_id)
    if not question or question.status != "failed":
        raise BizError(400, "仅失败题目可重试")
    if not question.answer_text:
        raise BizError(400, "无作答内容")
    try:
        result = default_judge_answer(question.question_text, question.answer_text)
        question.ai_score = result["score"]
        question.ai_feedback_json = result
        question.status = "graded"
    except LLMError:
        question.status = "failed"
    db.commit()
    return ok({"id": question.id, "status": question.status})


@router.post("/sessions/{session_id}/finish")
def finish_session(session_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    session = db.get(AiInterviewSession, session_id)
    if not session:
        raise BizError(404, "面试不存在")
    if user.role not in ADMIN_ROLES and session.candidate_user_id != user.id:
        raise BizError(403, "无权结束")
    rows = db.execute(
        select(AiInterviewQuestion).where(AiInterviewQuestion.session_id == session_id)
    ).scalars().all()
    scored = [q.ai_score for q in rows if q.ai_score is not None]
    if scored:
        session.overall_score = sum(scored) / len(scored)
    session.status = "completed"
    session.finished_at = datetime.now()
    _event(db, session.id, "finished", user.id, note=f"scored={len(scored)}/{len(rows)}")
    db.commit()
    return ok({"id": session.id, "overall_score": session.overall_score})
