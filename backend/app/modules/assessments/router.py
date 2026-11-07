"""C 测评域 HTTP 路由（prefix `/api/assessments`，由 A 在 main.py 注册）。

所有成功响应使用 `app.core.response.ok/paginated`；业务错误由 service 抛 `BizError`。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination
from app.core.response import ok, paginated
from app.database import get_db
from app.modules.assessments import service
from app.modules.assessments.models import (
    AssessmentAnswer,
    AssessmentAssignment,
    AssessmentPaper,
    AssessmentPlan,
    AssessmentQuestion,
    AssessmentReviewItem,
)
from app.modules.assessments.schemas import (
    AnswerSaveRequest,
    AssignmentCreateRequest,
    ManualScoreRequest,
    PlanCreateRequest,
    PlanUpdateRequest,
    QuestionCreateRequest,
    QuestionGenerateRequest,
    QuestionUpdateRequest,
)

router = APIRouter(prefix="/api/assessments", tags=["assessments"])


def _question_out(row: AssessmentQuestion) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "job_version": row.job_version,
        "job_title": row.job_title,
        "dimension_name": row.dimension_name,
        "dimension_type": row.dimension_type,
        "difficulty": row.difficulty,
        "question_type": row.question_type,
        "stem": row.stem,
        "options": list(row.options_json or []),
        "correct_answer": row.correct_answer,
        "reference_points": row.reference_points,
        "scoring_guideline": row.scoring_guideline,
        "status": row.status,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _plan_out(row: AssessmentPlan, paper: AssessmentPaper | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "purpose": row.purpose,
        "job_id": row.job_id,
        "job_title": row.job_title,
        "job_version": row.job_version,
        "dimensions": list(row.dimensions_json or []),
        "total_question_count": row.total_question_count,
        "pass_score": float(row.pass_score),
        "difficulty_structure": dict(row.difficulty_structure_json or {}),
        "status": row.status,
        "mode": row.mode,
        "strategy": dict(row.strategy_json or {}),
        "version": row.version,
        "published_at": row.published_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    snapshot = dict(row.bank_snapshot_json or {})
    if snapshot:
        data["bank_snapshot"] = {
            "question_count": snapshot.get("question_count"),
            "frozen_at": snapshot.get("frozen_at"),
        }
    if paper is not None:
        data["paper"] = {
            "id": paper.id,
            "plan_version": paper.plan_version,
            "question_count": paper.question_count,
            "dimensions": list(paper.dimensions_json or []),
        }
    return data


def _assignment_item(row: AssessmentAssignment) -> dict[str, Any]:
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "plan_version": row.plan_version,
        "purpose": row.purpose,
        "user_id": row.user_id,
        "talent_profile_id": row.talent_profile_id,
        "job_id": row.job_id,
        "job_title": row.job_title,
        "status": row.status,
        "mode": row.mode,
        "target_question_count": row.target_question_count,
        "attempt_no": row.attempt_no,
        "answers_open": bool(row.answers_open),
        "published_result_version": row.published_result_version,
        "started_at": row.started_at,
        "submitted_at": row.submitted_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
    }


def _answer_block(answer, visible: bool) -> dict[str, Any] | None:
    if answer is None:
        return None
    return {
        "selected_option": answer.selected_option,
        "answer_text": answer.answer_text,
        "grading_status": answer.grading_status,
        "score": float(answer.score) if (visible and answer.score is not None) else None,
    }


def _assignment_detail(db: Session, assignment: AssessmentAssignment, user) -> dict[str, Any]:
    visible = service._answer_visible(assignment, user)
    answers = {
        row.question_id: row
        for row in db.query(AssessmentAnswer)
        .filter(AssessmentAnswer.assignment_id == assignment.id)
        .all()
    }
    review_items = {
        row.question_id: row
        for row in db.query(AssessmentReviewItem)
        .filter(
            AssessmentReviewItem.assignment_id == assignment.id,
            AssessmentReviewItem.user_id == assignment.user_id,
        )
        .all()
    }
    questions: list[dict[str, Any]] = []
    paper_info: dict[str, Any] | None = None
    if assignment.mode == "adaptive":
        selections = service._selection_rows(db, assignment.id)
        for selection in selections:
            question = dict(selection.question_snapshot_json or {})
            question["sequence_no"] = selection.sequence_no
            if service.is_admin(user):
                question["selection_reason"] = dict(selection.selection_reason_json or {})
            if not visible:
                question = service.question_public_snapshot(question)
            else:
                completed = review_items.get(selection.question_id)
                question = {
                    **question,
                    "review_completed": bool(completed.is_completed) if completed else False,
                }
            question["answer"] = _answer_block(answers.get(selection.question_id), visible)
            questions.append(question)
    else:
        paper = service.get_paper(db, assignment)
        paper_info = {
            "id": paper.id,
            "question_count": paper.question_count,
            "pass_score": float(paper.pass_score),
            "dimensions": list(paper.dimensions_json or []),
        }
        for item in paper.questions_json or []:
            question = dict(item)
            question_id = int(question["question_id"])
            if not visible:
                question = service.question_public_snapshot(question)
            else:
                completed = review_items.get(question_id)
                question = {
                    **question,
                    "review_completed": bool(completed.is_completed) if completed else False,
                }
            question["answer"] = _answer_block(answers.get(question_id), visible)
            questions.append(question)

    visible_result = service.visible_result(db, assignment, user)
    latest = service._latest_result(db, assignment.id)
    return {
        "assignment": _assignment_item(assignment),
        "mode": assignment.mode,
        "paper": paper_info,
        "questions": questions,
        "result": service.public_result(visible_result) if visible_result else None,
        "result_available": (latest is not None) if service.is_admin(user) else False,
        "answers_visible": visible,
    }


def _result_out(db: Session, assignment: AssessmentAssignment, user) -> dict[str, Any]:
    data = service.get_result(db, assignment.id, user)
    payload: dict[str, Any] = {
        "assignment_id": assignment.id,
        "purpose": assignment.purpose,
        **_result_payload(data["result"]),
        "published": data["published"],
        "answers_open": data["answers_open"],
    }
    if data["review_items"] is not None:
        payload["review_items"] = [
            {
                "question_id": row.question_id,
                "is_completed": bool(row.is_completed),
                "completed_at": row.completed_at,
                "question_snapshot": dict(row.question_snapshot_json or {}),
            }
            for row in data["review_items"]
        ]
    return payload


def _result_payload(result) -> dict[str, Any]:
    return service.public_result(result)


def _visible_result_payload(db: Session, assignment_id: int, result, user) -> dict[str, Any] | None:
    """交卷/重试响应中的结果也必须按公开规则裁剪，不能提前把招聘成绩给候选人。"""
    if result is None:
        return None
    assignment = db.get(AssessmentAssignment, assignment_id)
    if service.is_admin(user):
        return _result_payload(result)
    if assignment is not None and assignment.purpose == "employee":
        return _result_payload(result)
    if assignment is not None and assignment.published_result_version == result.version:
        return _result_payload(result)
    return None


# ---------------------------------------------------------------- 题库

@router.post("/questions", summary="新建题目（待审核）")
def create_question(
    payload: QuestionCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    row = service.create_question(db, payload, user_id=user.id)
    return ok(_question_out(row))


@router.get("/questions", summary="题库列表")
def list_questions(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    job_id: int | None = Query(None),
    dimension_name: str | None = Query(None, max_length=100),
    question_type: str | None = Query(None),
    difficulty: str | None = Query(None),
    status: str | None = Query(None),
    keyword: str | None = Query(None, max_length=100),
):
    service.require_admin(user)
    rows, total = service.list_questions(
        db,
        job_id=job_id,
        dimension_name=dimension_name,
        question_type=question_type,
        difficulty=difficulty,
        status=status,
        keyword=keyword,
        offset=page.offset,
        limit=page.limit,
    )
    return paginated([_question_out(row) for row in rows], total, page.page, page.page_size)


@router.patch("/questions/{question_id}", summary="修改题目（重新进入待审核）")
def update_question(
    question_id: int,
    payload: QuestionUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    row = service.update_question(db, question_id, payload, user_id=user.id)
    return ok(_question_out(row))


@router.post("/questions/{question_id}/approve", summary="审核通过题目")
def approve_question(
    question_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    row = service.approve_question(db, question_id, user_id=user.id)
    return ok(_question_out(row))


@router.post("/questions/generate-draft", summary="模型生成待审核题目（自适应题库供给）")
def generate_question_drafts(
    payload: QuestionGenerateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    rows = service.generate_question_drafts(db, payload, user_id=user.id)
    return ok([_question_out(row) for row in rows])


# ---------------------------------------------------------------- 方案与发布

@router.post("/plans", summary="新建测评方案")
def create_plan(
    payload: PlanCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    row = service.create_plan(db, payload, user_id=user.id)
    return ok(_plan_out(row))


@router.get("/plans", summary="测评方案列表")
def list_plans(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    purpose: str | None = Query(None),
    status: str | None = Query(None),
    job_id: int | None = Query(None),
):
    service.require_admin(user)
    rows, total = service.list_plans(
        db, purpose=purpose, status=status, job_id=job_id, offset=page.offset, limit=page.limit
    )
    return paginated([_plan_out(row) for row in rows], total, page.page, page.page_size)


@router.get("/plans/{plan_id}", summary="测评方案详情")
def get_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    row = service.get_plan(db, plan_id)
    return ok(_plan_out(row))


@router.patch("/plans/{plan_id}", summary="修改草稿方案")
def update_plan(
    plan_id: int,
    payload: PlanUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    row = service.update_plan(db, plan_id, payload, user_id=user.id)
    return ok(_plan_out(row))


@router.post("/plans/{plan_id}/publish", summary="发布方案并冻结固定试卷")
def publish_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    plan, paper = service.publish_plan(db, plan_id, user_id=user.id)
    return ok(_plan_out(plan, paper))


# ---------------------------------------------------------------- 分配与作答

@router.post("/assignments", summary="分配测评（幂等键）")
def create_assignment(
    payload: AssignmentCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    row = service.create_assignment(db, payload, user_id=user.id)
    return ok(_assignment_item(row))


@router.get("/assignments", summary="测评任务列表（管理员）")
def list_assignments(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    purpose: str | None = Query(None),
    status: str | None = Query(None),
    plan_id: int | None = Query(None),
    user_id: int | None = Query(None),
):
    rows, total = service.list_assignments(
        db,
        admin_user=user,
        purpose=purpose,
        status=status,
        plan_id=plan_id,
        user_id=user_id,
        offset=page.offset,
        limit=page.limit,
    )
    return paginated([_assignment_item(row) for row in rows], total, page.page, page.page_size)


@router.get("/my/assignments", summary="我的测评任务")
def my_assignments(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.list_my_assignments(db, user)
    return ok({"items": [_assignment_item(row) for row in rows], "total": len(rows)})


@router.get("/assignments/{assignment_id}", summary="测评任务详情（按答案开放裁剪）")
def get_assignment(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    assignment = service.get_assignment(db, assignment_id, user)
    return ok(_assignment_detail(db, assignment, user))


@router.post("/assignments/{assignment_id}/start", summary="开始测评")
def start_assignment(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.start_assignment(db, assignment_id, user)
    return ok(_assignment_item(row))


@router.post("/assignments/{assignment_id}/next-question", summary="自适应获取下一题（幂等）")
def next_question(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(service.next_question(db, assignment_id, user))


@router.get("/assignments/{assignment_id}/selections", summary="自适应选题留痕")
def list_selections(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.list_selections(db, assignment_id, user)
    include_reason = service.is_admin(user)
    return ok(
        {
            "items": [
                {
                    "sequence_no": row.sequence_no,
                    "question_id": row.question_id,
                    "dimension_name": row.dimension_name,
                    "difficulty": row.difficulty,
                    "question_snapshot": dict(row.question_snapshot_json or {}),
                    "selection_reason": (
                        dict(row.selection_reason_json or {}) if include_reason else None
                    ),
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            "total": len(rows),
        }
    )


@router.put("/assignments/{assignment_id}/answers/{question_id}", summary="保存单题答案")
def save_answer(
    assignment_id: int,
    question_id: int,
    payload: AnswerSaveRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.save_answer(db, assignment_id, question_id, payload, user)
    return ok(
        {
            "assignment_id": row.assignment_id,
            "question_id": row.question_id,
            "selected_option": row.selected_option,
            "answer_text": row.answer_text,
            "grading_status": row.grading_status,
            "updated_at": row.updated_at,
        }
    )


@router.get("/assignments/{assignment_id}/answers", summary="查看已保存答案")
def get_answers(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    assignment = service.get_assignment(db, assignment_id, user)
    visible = service._answer_visible(assignment, user)
    rows = service.get_answers(db, assignment_id, user)
    return ok(
        {
            "items": [
                {
                    "question_id": row.question_id,
                    "selected_option": row.selected_option,
                    "answer_text": row.answer_text,
                    "grading_status": row.grading_status,
                    "score": float(row.score) if visible and row.score is not None else None,
                    "is_correct": row.is_correct if visible else None,
                }
                for row in rows
            ],
            "total": len(rows),
        }
    )


@router.post("/assignments/{assignment_id}/submit", summary="交卷（幂等评分）")
def submit_assignment(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    result = service.submit_assignment(db, assignment_id, user)
    return ok(
        {
            "assignment_id": result["assignment_id"],
            "status": result["status"],
            "failed_count": result.get("failed_count", 0),
            "result": _visible_result_payload(db, result["assignment_id"], result["result"], user),
        }
    )


@router.post("/assignments/{assignment_id}/retry-grading", summary="只重试失败的评分项")
def retry_grading(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    result = service.retry_grading(db, assignment_id, user)
    return ok(
        {
            "assignment_id": result["assignment_id"],
            "status": result["status"],
            "retried": result["retried"],
            "failed_count": result["failed_count"],
            "result": _visible_result_payload(db, result["assignment_id"], result["result"], user),
        }
    )


@router.get("/assignments/{assignment_id}/result", summary="查看测评结果")
def get_result(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    assignment = service.get_assignment(db, assignment_id, user)
    return ok(_result_out(db, assignment, user))


@router.post("/assignments/{assignment_id}/publish", summary="公布成绩（绑定结果版本）")
def publish_result(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.publish_result(db, assignment_id, user)
    return ok(
        {
            "assignment_id": row.id,
            "published_result_version": row.published_result_version,
            "status": row.status,
        }
    )


@router.post("/assignments/{assignment_id}/open-answers", summary="开放标准答案与复习入口")
def open_answers(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.open_answers(db, assignment_id, user)
    return ok({"assignment_id": row.id, "answers_open": bool(row.answers_open)})


@router.patch(
    "/assignments/{assignment_id}/answers/{question_id}/score",
    summary="人工修分（必填理由，生成新结果版本）",
)
def manual_score(
    assignment_id: int,
    question_id: int,
    payload: ManualScoreRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    data = service.manual_score(db, assignment_id, question_id, payload, user)
    return ok(
        {
            "assignment_id": assignment_id,
            "review_id": data["review_id"],
            "result": _result_payload(data["result"]) if data["result"] else None,
            "message": data.get("message", "已重新计算并生成新结果版本"),
        }
    )


@router.post(
    "/assignments/{assignment_id}/review-items/{question_id}/complete",
    summary="标记复习完成",
)
def complete_review_item(
    assignment_id: int,
    question_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.complete_review_item(db, assignment_id, question_id, user)
    return ok(
        {
            "assignment_id": row.assignment_id,
            "question_id": row.question_id,
            "is_completed": bool(row.is_completed),
            "completed_at": row.completed_at,
        }
    )
