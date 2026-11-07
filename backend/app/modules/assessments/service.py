"""C 测评域服务：题库、方案、固定试卷、分配、答题、评分、修分与公开（契约 §3/§4）。

状态口径：
- assignment: `assigned` → `in_progress` → `grading`（有失败项）→ `completed`；`cancelled` 保留扩展。
- 交卷幂等：已有结果直接返回；失败项不重复评分，只由 `retry_grading` 补评。
- 招聘公开绑定 `published_result_version`；员工评分完成即本人可见。
- 修分必须填写理由，生成新的结果版本；招聘新版本不继承旧公开标记。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.models import User
from app.modules.assessments.adaptive import (
    build_ability,
    choose_next_question,
    ending_reason,
    validate_strategy,
)
from app.modules.assessments.generation import (
    GENERATION_PROMPT_VERSION,
    default_generate_questions,
)
from app.modules.assessments.grading import GRADING_PROMPT_VERSION, default_grade_short_answer
from app.modules.assessments.models import (
    AssessmentAnswer,
    AssessmentAssignment,
    AssessmentPaper,
    AssessmentPlan,
    AssessmentQuestion,
    AssessmentResult,
    AssessmentReview,
    AssessmentReviewItem,
    AssessmentSelection,
)
from app.modules.assessments.scoring import (
    DEFAULT_DIFFICULTY_STRUCTURE,
    allocate_difficulty_slots,
    allocate_question_counts,
    compute_result,
    grade_single_choice,
    parse_dimensions,
    validate_difficulty_structure,
    validate_plan_input,
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
from app.modules.training.models import TrainingGrowthEvent

ADMIN_ROLES = frozenset({"admin", "hr"})
ASSIGNMENT_STATUSES = frozenset({"assigned", "in_progress", "grading", "completed", "cancelled"})
PLAN_STATUSES = frozenset({"draft", "published", "archived"})
QUESTION_STATUSES = frozenset({"pending", "approved", "disabled"})

Grader = Callable[[dict[str, Any], str], float]


# ---------------------------------------------------------------- 权限

def is_admin(user: User) -> bool:
    return user.role in ADMIN_ROLES


def require_admin(user: User) -> None:
    if not is_admin(user):
        raise BizError(403, "仅管理员可执行该操作")


def _require_owner_or_admin(user: User, assignment: AssessmentAssignment) -> None:
    if is_admin(user):
        return
    if assignment.user_id != user.id:
        raise BizError(403, "无权访问他人的测评任务")


# ---------------------------------------------------------------- 题库

def _validate_question(payload: QuestionCreateRequest | QuestionUpdateRequest, *, partial: bool) -> None:
    """题型级校验：单选必须有选项与标准答案；简答必须有评分说明。"""
    fields = payload.model_dump(exclude_unset=True) if partial else payload.model_dump()
    qtype = fields.get("question_type", "single_choice")
    if not partial or "options" in fields or "correct_answer" in fields or "question_type" in fields:
        options = fields.get("options")
        answer = (fields.get("correct_answer") or "").strip()
        if qtype == "single_choice":
            if options is None or len(options) < 2:
                raise BizError(400, "单选题至少需要 2 个选项")
            if not answer:
                raise BizError(400, "单选题必须填写标准答案")
            if answer not in [str(item).strip() for item in options]:
                raise BizError(400, "单选标准答案必须与某个选项完全一致")
    if qtype == "short_answer" and (not partial or "question_type" in fields or "scoring_guideline" in fields):
        guideline = (fields.get("scoring_guideline") or "").strip()
        if not partial and not guideline:
            raise BizError(400, "简答题必须填写评分说明，供模型与人工复核使用")


def create_question(db: Session, payload: QuestionCreateRequest, *, user_id: int) -> AssessmentQuestion:
    _validate_question(payload, partial=False)
    row = AssessmentQuestion(
        job_id=payload.job_id,
        job_version=payload.job_version,
        job_title=(payload.job_title or "").strip() or None,
        dimension_name=payload.dimension_name.strip(),
        dimension_type=payload.dimension_type,
        difficulty=payload.difficulty,
        question_type=payload.question_type,
        stem=payload.stem,
        options_json=payload.options,
        correct_answer=(payload.correct_answer or "").strip() or None,
        reference_points=payload.reference_points,
        scoring_guideline=payload.scoring_guideline,
        status="pending",
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_question(
    db: Session, question_id: int, payload: QuestionUpdateRequest, *, user_id: int
) -> AssessmentQuestion:
    row = db.get(AssessmentQuestion, question_id)
    if row is None:
        raise BizError(404, "题目不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        target = {
            "job_title": "job_title",
            "dimension_name": "dimension_name",
            "dimension_type": "dimension_type",
            "difficulty": "difficulty",
            "question_type": "question_type",
            "stem": "stem",
            "options": "options_json",
            "correct_answer": "correct_answer",
            "reference_points": "reference_points",
            "scoring_guideline": "scoring_guideline",
            "job_id": "job_id",
            "job_version": "job_version",
        }[key]
        if key == "dimension_name":
            value = str(value).strip()
        if key == "correct_answer":
            value = (value or "").strip() or None
        setattr(row, target, value)
    # 用合并后的完整题目再校验，避免修改类型后残留不合法选项
    _validate_question(
        QuestionCreateRequest(
            job_id=row.job_id,
            job_version=row.job_version,
            job_title=row.job_title,
            dimension_name=row.dimension_name,
            dimension_type=row.dimension_type,
            difficulty=row.difficulty,
            question_type=row.question_type,
            stem=row.stem,
            options=list(row.options_json or []) or None,
            correct_answer=row.correct_answer,
            reference_points=row.reference_points,
            scoring_guideline=row.scoring_guideline,
        ),
        partial=False,
    )
    # 内容被修改后需重新审核，避免已发布试卷外的题目状态漂移
    row.status = "pending"
    row.updated_by = user_id
    db.commit()
    db.refresh(row)
    return row


def approve_question(db: Session, question_id: int, *, user_id: int) -> AssessmentQuestion:
    row = db.get(AssessmentQuestion, question_id)
    if row is None:
        raise BizError(404, "题目不存在")
    if row.question_type == "single_choice":
        _validate_question(
            QuestionCreateRequest(
                dimension_name=row.dimension_name,
                question_type=row.question_type,
                stem=row.stem,
                options=list(row.options_json or []),
                correct_answer=row.correct_answer,
            ),
            partial=False,
        )
    else:
        if not (row.scoring_guideline or "").strip():
            raise BizError(400, "简答题必须填写评分说明后才能审核通过")
    row.status = "approved"
    row.updated_by = user_id
    db.commit()
    db.refresh(row)
    return row


def list_questions(
    db: Session,
    *,
    job_id: int | None = None,
    dimension_name: str | None = None,
    question_type: str | None = None,
    difficulty: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[AssessmentQuestion], int]:
    stmt: Select = select(AssessmentQuestion)
    if job_id is not None:
        stmt = stmt.where(AssessmentQuestion.job_id == job_id)
    if dimension_name:
        stmt = stmt.where(AssessmentQuestion.dimension_name == dimension_name)
    if question_type:
        stmt = stmt.where(AssessmentQuestion.question_type == question_type)
    if difficulty:
        stmt = stmt.where(AssessmentQuestion.difficulty == difficulty)
    if status:
        stmt = stmt.where(AssessmentQuestion.status == status)
    if keyword:
        stmt = stmt.where(AssessmentQuestion.stem.like(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(AssessmentQuestion.id.desc()).offset(offset).limit(limit)))
    return rows, int(total)


def generate_question_drafts(
    db: Session,
    payload: QuestionGenerateRequest,
    *,
    user_id: int,
    generator: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> list[AssessmentQuestion]:
    """管理员触发真实模型生成待审核题目；失败 503，不伪造题目。"""
    effective = generator or default_generate_questions
    request = {
        "job_title": payload.job_title,
        "dimension_name": payload.dimension_name,
        "dimension_type": payload.dimension_type,
        "difficulty": payload.difficulty,
        "question_type": payload.question_type,
        "count": payload.count,
        "prompt_version": GENERATION_PROMPT_VERSION,
    }
    try:
        items = effective(request)
    except Exception as exc:  # noqa: BLE001 - 模型失败必须明确为 503，而不是静默造题
        raise BizError(503, f"题目生成失败：{type(exc).__name__}") from exc
    if not items:
        raise BizError(503, "模型未返回题目，请稍后重试")
    created: list[AssessmentQuestion] = []
    prepared: list[QuestionCreateRequest] = []
    for item in items[: payload.count]:
        try:
            data = QuestionCreateRequest(
                job_id=payload.job_id,
                job_version=payload.job_version,
                job_title=payload.job_title,
                dimension_name=payload.dimension_name,
                dimension_type=payload.dimension_type,
                difficulty=payload.difficulty,
                question_type=payload.question_type,
                stem=str(item.get("stem") or "").strip(),
                options=item.get("options"),
                correct_answer=item.get("correct_answer"),
                reference_points=item.get("reference_points"),
                scoring_guideline=item.get("scoring_guideline"),
            )
            _validate_question(data, partial=False)
        except BizError as exc:
            raise BizError(503, f"模型返回的题目未通过校验：{exc.message}") from exc
        except Exception as exc:  # noqa: BLE001 - 模型输出结构不合法
            raise BizError(503, "模型返回的题目结构不合法，请重试") from exc
        prepared.append(data)
    for data in prepared:
        created.append(create_question(db, data, user_id=user_id))
    return created


# ---------------------------------------------------------------- 方案与固定试卷

def _plan_dimensions(payload_dimensions: list) -> list[dict[str, Any]]:
    return [
        {"name": item.name.strip(), "type": item.type, "weight": float(item.weight)}
        for item in payload_dimensions
    ]


def create_plan(db: Session, payload: PlanCreateRequest, *, user_id: int) -> AssessmentPlan:
    structure = validate_difficulty_structure(payload.difficulty_structure or {})
    dimensions = _plan_dimensions(payload.dimensions)
    validate_plan_input(
        parse_dimensions(dimensions), payload.total_question_count, payload.pass_score, structure
    )
    strategy = (
        validate_strategy(
            payload.strategy,
            total_question_count=payload.total_question_count,
            dimension_count=len(dimensions),
        )
        if payload.mode == "adaptive"
        else None
    )
    row = AssessmentPlan(
        purpose=payload.purpose,
        job_id=payload.job_id,
        job_title=payload.job_title.strip(),
        job_version=payload.job_version,
        dimensions_json=dimensions,
        total_question_count=payload.total_question_count,
        pass_score=round(float(payload.pass_score), 2),
        difficulty_structure_json=structure,
        status="draft",
        mode=payload.mode,
        strategy_json=strategy,
        version=1,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_plan(db: Session, plan_id: int, payload: PlanUpdateRequest, *, user_id: int) -> AssessmentPlan:
    row = db.get(AssessmentPlan, plan_id)
    if row is None:
        raise BizError(404, "测评方案不存在")
    if row.status != "draft":
        raise BizError(400, "已发布方案不可直接修改，请新建方案或先归档")
    fields = payload.model_dump(exclude_unset=True)
    if "job_title" in fields:
        row.job_title = fields["job_title"].strip()
    if "job_version" in fields:
        row.job_version = int(fields["job_version"])
    if "dimensions" in fields:
        row.dimensions_json = _plan_dimensions(payload.dimensions or [])
    if "total_question_count" in fields:
        row.total_question_count = int(fields["total_question_count"])
    if "pass_score" in fields:
        row.pass_score = round(float(fields["pass_score"]), 2)
    if "difficulty_structure" in fields:
        row.difficulty_structure_json = validate_difficulty_structure(
            fields["difficulty_structure"] or {}
        )
    if "mode" in fields:
        row.mode = fields["mode"]
    validate_plan_input(
        parse_dimensions(list(row.dimensions_json)),
        row.total_question_count,
        float(row.pass_score),
        dict(row.difficulty_structure_json),
    )
    if row.mode == "adaptive":
        raw_strategy = fields.get("strategy", dict(row.strategy_json or {}))
        row.strategy_json = validate_strategy(
            dict(raw_strategy or {}),
            total_question_count=row.total_question_count,
            dimension_count=len(row.dimensions_json or []),
        )
    else:
        row.strategy_json = None
    row.updated_by = user_id
    db.commit()
    db.refresh(row)
    return row


def get_plan(db: Session, plan_id: int) -> AssessmentPlan:
    row = db.get(AssessmentPlan, plan_id)
    if row is None:
        raise BizError(404, "测评方案不存在")
    return row


def list_plans(
    db: Session,
    *,
    purpose: str | None = None,
    status: str | None = None,
    job_id: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[AssessmentPlan], int]:
    stmt: Select = select(AssessmentPlan)
    if purpose:
        stmt = stmt.where(AssessmentPlan.purpose == purpose)
    if status:
        stmt = stmt.where(AssessmentPlan.status == status)
    if job_id is not None:
        stmt = stmt.where(AssessmentPlan.job_id == job_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(AssessmentPlan.id.desc()).offset(offset).limit(limit)))
    return rows, int(total)


def _select_questions(
    db: Session, plan: AssessmentPlan
) -> tuple[list[AssessmentQuestion], dict[str, Any]]:
    """按维度题量 + 难度结构从已审核题库固定选卷；不足则列缺口拒绝发布。"""
    dimensions = parse_dimensions(list(plan.dimensions_json))
    counts = allocate_question_counts(dimensions, plan.total_question_count)
    structure = dict(plan.difficulty_structure_json or DEFAULT_DIFFICULTY_STRUCTURE)
    selected: list[AssessmentQuestion] = []
    allocation: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    used_ids: set[int] = set()

    for dimension, need in zip(dimensions, counts, strict=True):
        slots = allocate_difficulty_slots(need, structure)
        stmt = select(AssessmentQuestion).where(
            AssessmentQuestion.status == "approved",
            AssessmentQuestion.dimension_name == dimension.name,
            AssessmentQuestion.dimension_type == dimension.type,
        )
        pool = list(
            db.scalars(
                stmt.order_by(
                    AssessmentQuestion.job_id.is_(None),
                    AssessmentQuestion.id.asc(),
                )
            )
        )
        dimension_selected: list[AssessmentQuestion] = []
        for difficulty in ("easy", "medium", "hard"):
            slots_needed = slots[difficulty]
            if slots_needed <= 0:
                continue
            candidates = [
                item for item in pool
                if item.difficulty == difficulty and item.id not in used_ids
            ]
            take = candidates[:slots_needed]
            if len(take) < slots_needed:
                gaps.append(
                    {
                        "dimension": dimension.name,
                        "difficulty": difficulty,
                        "required": slots_needed,
                        "available": len(take),
                    }
                )
            for item in take:
                used_ids.add(item.id)
                dimension_selected.append(item)
        selected.extend(dimension_selected)
        allocation.append(
            {
                "name": dimension.name,
                "type": dimension.type,
                "weight": dimension.weight,
                "question_count": need,
                "difficulty": slots,
            }
        )

    if gaps:
        detail = "；".join(
            f"维度「{gap['dimension']}」难度「{gap['difficulty']}」需要 {gap['required']} 题，可用 {gap['available']} 题"
            for gap in gaps
        )
        raise BizError(400, f"题库不足，无法发布：{detail}")
    return selected, {"allocation": allocation, "structure": structure}


def _paper_question_snapshot(question: AssessmentQuestion) -> dict[str, Any]:
    return {
        "question_id": question.id,
        "dimension_name": question.dimension_name,
        "dimension_type": question.dimension_type,
        "difficulty": question.difficulty,
        "question_type": question.question_type,
        "stem": question.stem,
        "options": list(question.options_json or []),
        "correct_answer": question.correct_answer,
        "reference_points": question.reference_points,
        "scoring_guideline": question.scoring_guideline,
        "job_id": question.job_id,
        "job_version": question.job_version,
    }


def _bank_candidates(db: Session, plan: AssessmentPlan) -> list[AssessmentQuestion]:
    """方案维度可用的已审核题目（自适应题库快照来源）。"""
    dimensions = parse_dimensions(list(plan.dimensions_json))
    pairs = {(item.name, item.type) for item in dimensions}
    rows = list(
        db.scalars(select(AssessmentQuestion).where(AssessmentQuestion.status == "approved"))
    )
    return [row for row in rows if (row.dimension_name, row.dimension_type) in pairs]


def _publish_adaptive(db: Session, plan: AssessmentPlan, *, user_id: int) -> AssessmentPlan:
    """自适应发布：冻结策略与题库快照，不生成固定试卷。"""
    dimensions = parse_dimensions(list(plan.dimensions_json))
    strategy = validate_strategy(
        dict(plan.strategy_json or {}),
        total_question_count=plan.total_question_count,
        dimension_count=len(dimensions),
    )
    bank = _bank_candidates(db, plan)
    missing = [item.name for item in dimensions if not any(q.dimension_name == item.name for q in bank)]
    if missing:
        raise BizError(400, f"题库不足，无法发布：维度「{'、'.join(missing)}」缺少已审核题目")
    if len(bank) < plan.total_question_count:
        raise BizError(
            400,
            f"题库不足，无法发布：自适应方案至少需要 {plan.total_question_count} 道已审核题目，"
            f"当前可用 {len(bank)} 道",
        )
    plan.strategy_json = strategy
    plan.bank_snapshot_json = {
        "mode": "adaptive",
        "question_ids": sorted(int(q.id) for q in bank),
        "question_count": len(bank),
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
    }
    plan.status = "published"
    plan.published_at = datetime.now()
    plan.updated_by = user_id
    db.commit()
    db.refresh(plan)
    return plan


def publish_plan(db: Session, plan_id: int, *, user_id: int) -> tuple[AssessmentPlan, AssessmentPaper | None]:
    plan = get_plan(db, plan_id)
    if plan.status == "published":
        paper = db.scalar(
            select(AssessmentPaper).where(
                AssessmentPaper.plan_id == plan.id,
                AssessmentPaper.plan_version == plan.version,
            )
        )
        if plan.mode != "fixed" or paper is not None:
            return plan, paper
    validate_plan_input(
        parse_dimensions(list(plan.dimensions_json)),
        plan.total_question_count,
        float(plan.pass_score),
        dict(plan.difficulty_structure_json),
    )
    if plan.mode == "adaptive":
        return _publish_adaptive(db, plan, user_id=user_id), None
    questions, allocation = _select_questions(db, plan)
    if len(questions) != plan.total_question_count:
        raise BizError(500, "组卷题量与方案总题量不一致，已阻止发布")
    paper = AssessmentPaper(
        plan_id=plan.id,
        plan_version=plan.version,
        purpose=plan.purpose,
        job_id=plan.job_id,
        job_title=plan.job_title,
        question_count=len(questions),
        total_score=100.0,
        pass_score=plan.pass_score,
        dimensions_json=allocation["allocation"],
        questions_json=[_paper_question_snapshot(item) for item in questions],
        grading_rule_json={
            "single_choice": "selected_option 与 correct_answer 精确匹配，正确 100、否则 0",
            "short_answer": "真实模型按 scoring_guideline 评分 0–100",
            "dimension_score": "维度题目得分均值（每题满分 100）",
            "total_score": "Σ(维度分×权重)/100，未舍入值与总分线比较，含相等通过",
            "prompt_version": GRADING_PROMPT_VERSION,
        },
        created_by=user_id,
    )
    db.add(paper)
    plan.status = "published"
    plan.published_at = datetime.now()
    plan.updated_by = user_id
    try:
        db.commit()
    except IntegrityError as exc:  # 同一方案版本并发发布：回滚后返回既有试卷
        db.rollback()
        paper = db.scalar(
            select(AssessmentPaper).where(
                AssessmentPaper.plan_id == plan.id,
                AssessmentPaper.plan_version == plan.version,
            )
        )
        if paper is None:
            raise BizError(409, "该方案版本已发布，请刷新后查看") from exc
    db.refresh(plan)
    db.refresh(paper)
    return plan, paper


# ---------------------------------------------------------------- 分配与作答

def get_paper(db: Session, assignment: AssessmentAssignment) -> AssessmentPaper:
    if assignment.paper_id is None:
        raise BizError(400, "自适应测评不绑定固定试卷，请使用 next-question 逐题作答")
    paper = db.get(AssessmentPaper, assignment.paper_id)
    if paper is None:
        raise BizError(404, "试卷不存在")
    return paper


def create_assignment(
    db: Session, payload: AssignmentCreateRequest, *, user_id: int
) -> AssessmentAssignment:
    plan = get_plan(db, payload.plan_id)
    if plan.status != "published":
        raise BizError(400, "方案未发布，不能分配测评")
    paper: AssessmentPaper | None = None
    strategy: dict[str, Any] | None = None
    target_question_count: int | None = None
    if plan.mode == "fixed":
        paper = db.scalar(
            select(AssessmentPaper).where(
                AssessmentPaper.plan_id == plan.id,
                AssessmentPaper.plan_version == plan.version,
            )
        )
        if paper is None:
            raise BizError(400, "方案已发布但缺少试卷快照，请联系管理员重新发布")
    else:
        if plan.bank_snapshot_json is None:
            raise BizError(400, "自适应方案缺少题库快照，请联系管理员重新发布")
        strategy = dict(plan.strategy_json or {})
        target_question_count = int(plan.total_question_count)

    key = (payload.idempotency_key or "").strip() or uuid.uuid4().hex
    existing = db.scalar(
        select(AssessmentAssignment).where(AssessmentAssignment.idempotency_key == key)
    )
    if existing is not None:
        return existing

    row = AssessmentAssignment(
        plan_id=plan.id,
        plan_version=plan.version,
        paper_id=paper.id if paper is not None else None,
        mode=plan.mode,
        strategy_snapshot_json=strategy,
        target_question_count=target_question_count,
        purpose=plan.purpose,
        user_id=payload.user_id,
        talent_profile_id=payload.talent_profile_id,
        job_id=payload.job_id if payload.job_id is not None else plan.job_id,
        job_title=(payload.job_title or "").strip() or plan.job_title,
        status="assigned",
        attempt_no=payload.attempt_no,
        idempotency_key=key,
        created_by=user_id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(AssessmentAssignment).where(AssessmentAssignment.idempotency_key == key)
        )
        if existing is None:
            raise BizError(409, "测评分配幂等键冲突，请重试") from exc
        return existing
    db.refresh(row)
    return row


def list_assignments(
    db: Session,
    *,
    admin_user: User,
    purpose: str | None = None,
    status: str | None = None,
    plan_id: int | None = None,
    user_id: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[AssessmentAssignment], int]:
    require_admin(admin_user)
    stmt: Select = select(AssessmentAssignment)
    if purpose:
        stmt = stmt.where(AssessmentAssignment.purpose == purpose)
    if status:
        stmt = stmt.where(AssessmentAssignment.status == status)
    if plan_id is not None:
        stmt = stmt.where(AssessmentAssignment.plan_id == plan_id)
    if user_id is not None:
        stmt = stmt.where(AssessmentAssignment.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(AssessmentAssignment.id.desc()).offset(offset).limit(limit)))
    return rows, int(total)


def list_my_assignments(db: Session, user: User) -> list[AssessmentAssignment]:
    return list(
        db.scalars(
            select(AssessmentAssignment)
            .where(AssessmentAssignment.user_id == user.id)
            .order_by(AssessmentAssignment.id.desc())
        )
    )


def get_assignment(db: Session, assignment_id: int, user: User) -> AssessmentAssignment:
    row = db.get(AssessmentAssignment, assignment_id)
    if row is None:
        raise BizError(404, "测评任务不存在")
    _require_owner_or_admin(user, row)
    return row


def _answer_visible(assignment: AssessmentAssignment, user: User) -> bool:
    if is_admin(user):
        return True
    if assignment.purpose == "employee" and assignment.status == "completed":
        return True
    return bool(assignment.answers_open)


def start_assignment(db: Session, assignment_id: int, user: User) -> AssessmentAssignment:
    row = get_assignment(db, assignment_id, user)
    if row.status == "completed":
        raise BizError(400, "测评已交卷，不能重新开始")
    if row.status == "cancelled":
        raise BizError(400, "测评已撤销，不能作答")
    if row.status == "assigned":
        row.status = "in_progress"
        row.started_at = datetime.now()
        db.commit()
        db.refresh(row)
    return row


def _paper_question(paper: AssessmentPaper, question_id: int) -> dict[str, Any]:
    for item in paper.questions_json or []:
        if int(item.get("question_id")) == question_id:
            return dict(item)
    raise BizError(400, "题目不属于当前试卷")


def _assignment_context(db: Session, assignment: AssessmentAssignment) -> dict[str, Any]:
    """固定模式取试卷快照，自适应模式取方案冻结信息，供评分、修分与能力分析。"""
    if assignment.mode == "fixed":
        paper = get_paper(db, assignment)
        return {
            "dimensions": parse_dimensions(list(paper.dimensions_json)),
            "pass_score": float(paper.pass_score),
            "plan_id": paper.plan_id,
            "plan_version": paper.plan_version,
            "paper_id": paper.id,
            "strategy": None,
        }
    plan = get_plan(db, assignment.plan_id)
    return {
        "dimensions": parse_dimensions(list(plan.dimensions_json)),
        "pass_score": float(plan.pass_score),
        "plan_id": plan.id,
        "plan_version": assignment.plan_version,
        "paper_id": None,
        "strategy": dict(assignment.strategy_snapshot_json or {}),
    }


def _selection_rows(db: Session, assignment_id: int) -> list[AssessmentSelection]:
    return list(
        db.scalars(
            select(AssessmentSelection)
            .where(AssessmentSelection.assignment_id == assignment_id)
            .order_by(AssessmentSelection.sequence_no.asc())
        )
    )


def _selection_answers(db: Session, assignment: AssessmentAssignment) -> list[AssessmentAnswer]:
    """自适应交卷：为每道已出题目建/取答案行，未作答的由评分环节记 0。"""
    existing = {
        row.question_id: row
        for row in db.scalars(
            select(AssessmentAnswer).where(AssessmentAnswer.assignment_id == assignment.id)
        )
    }
    for selection in _selection_rows(db, assignment.id):
        if selection.question_id in existing:
            continue
        row = AssessmentAnswer(
            assignment_id=assignment.id,
            question_id=selection.question_id,
            question_snapshot_json=dict(selection.question_snapshot_json or {}),
            grading_status="pending",
        )
        db.add(row)
        existing[selection.question_id] = row
    db.flush()
    return list(existing.values())


def _issue_payload(
    selection: AssessmentSelection,
    *,
    answered_count: int,
    target_question_count: int,
    done: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "done": done,
        "reason": reason,
        "answered_count": answered_count,
        "target_question_count": target_question_count,
        "sequence_no": selection.sequence_no,
    }
    if not done:
        payload["question"] = question_public_snapshot(dict(selection.question_snapshot_json or {}))
    return payload


def next_question(db: Session, assignment_id: int, user: User) -> dict[str, Any]:
    """自适应逐题出题：幂等返回当前未答题；达到结束规则返回 done。"""
    assignment = get_assignment(db, assignment_id, user)
    if assignment.mode != "adaptive":
        raise BizError(400, "固定模式请按试卷逐题作答")
    if assignment.status == "cancelled":
        raise BizError(400, "测评已撤销，不能作答")
    target = int(assignment.target_question_count or 0)
    if assignment.status == "completed" or assignment.status == "grading":
        return {
            "done": True,
            "reason": "assignment_" + assignment.status,
            "answered_count": len(_selection_rows(db, assignment.id)),
            "target_question_count": target,
        }
    if assignment.status == "assigned":
        assignment.status = "in_progress"
        assignment.started_at = assignment.started_at or datetime.now()
        db.commit()
        db.refresh(assignment)

    context = _assignment_context(db, assignment)
    dimensions = context["dimensions"]
    strategy = validate_strategy(
        dict(assignment.strategy_snapshot_json or {}),
        total_question_count=target,
        dimension_count=len(dimensions),
    )
    selections = _selection_rows(db, assignment.id)
    answers = {
        row.question_id: row
        for row in db.scalars(
            select(AssessmentAnswer).where(AssessmentAnswer.assignment_id == assignment.id)
        )
    }
    if selections and selections[-1].question_id not in answers:
        return _issue_payload(
            selections[-1],
            answered_count=len(answers),
            target_question_count=target,
        )

    asked = []
    for selection in selections:
        answer = answers.get(selection.question_id)
        score = None
        if answer is not None and answer.grading_status in {"graded", "manual"}:
            score = float(answer.score or 0)
        asked.append(
            {
                "question_id": selection.question_id,
                "dimension_name": selection.dimension_name,
                "difficulty": selection.difficulty,
                "score": score,
            }
        )
    pending_reason = ending_reason(
        asked_count=len(selections),
        covered_dimensions=sorted({item.dimension_name for item in selections}),
        dimensions=dimensions,
        strategy=strategy,
        target_question_count=target,
    )
    if pending_reason:
        return {
            "done": True,
            "reason": pending_reason,
            "answered_count": len(answers),
            "target_question_count": target,
        }

    plan = get_plan(db, assignment.plan_id)
    bank_ids = [int(item) for item in (plan.bank_snapshot_json or {}).get("question_ids") or []]
    rows = list(
        db.scalars(
            select(AssessmentQuestion).where(
                AssessmentQuestion.id.in_(bank_ids),
                AssessmentQuestion.status == "approved",
            )
        )
    ) if bank_ids else _bank_candidates(db, plan)
    candidates = [
        {
            "question_id": row.id,
            "dimension_name": row.dimension_name,
            "dimension_type": row.dimension_type,
            "difficulty": row.difficulty,
            "job_id": row.job_id,
        }
        for row in rows
    ]
    chosen = choose_next_question(
        candidates=candidates,
        asked=asked,
        dimensions=dimensions,
        strategy=strategy,
        target_question_count=target,
        seed=assignment.id,
        plan_job_id=assignment.job_id or plan.job_id,
    )
    if chosen is None:
        return {
            "done": True,
            "reason": "bank_exhausted",
            "answered_count": len(answers),
            "target_question_count": target,
        }
    question = db.get(AssessmentQuestion, int(chosen["question_id"]))
    if question is None:
        raise BizError(500, "选题结果对应的题目不存在，请联系管理员")
    selection = AssessmentSelection(
        assignment_id=assignment.id,
        sequence_no=len(selections) + 1,
        question_id=question.id,
        dimension_name=question.dimension_name,
        difficulty=question.difficulty,
        question_snapshot_json=_paper_question_snapshot(question),
        selection_reason_json=dict(chosen["reason"]),
        strategy_snapshot_json=strategy,
    )
    db.add(selection)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        latest = _selection_rows(db, assignment.id)
        if not latest:
            raise BizError(409, "出题并发冲突，请刷新后重试") from exc
        return _issue_payload(latest[-1], answered_count=len(answers), target_question_count=target)
    db.refresh(selection)
    return _issue_payload(selection, answered_count=len(answers), target_question_count=target)


def list_selections(
    db: Session, assignment_id: int, user: User
) -> list[AssessmentSelection]:
    """选题留痕：管理员始终可见；本人仅在答案开放后可见。"""
    assignment = get_assignment(db, assignment_id, user)
    if not is_admin(user) and not _answer_visible(assignment, user):
        raise BizError(403, "选题留痕尚未开放")
    return _selection_rows(db, assignment.id)


def save_answer(
    db: Session,
    assignment_id: int,
    question_id: int,
    payload: AnswerSaveRequest,
    user: User,
) -> AssessmentAnswer:
    assignment = get_assignment(db, assignment_id, user)
    if assignment.status in {"completed", "cancelled"}:
        raise BizError(400, "交卷后不能修改答案")
    if assignment.mode == "adaptive":
        selections = _selection_rows(db, assignment.id)
        if not selections:
            raise BizError(400, "请先获取题目再作答")
        if selections[-1].question_id != question_id:
            raise BizError(400, "只能作答当前题目；已出题的历史题目不可再修改")
        snapshot = dict(selections[-1].question_snapshot_json or {})
    else:
        snapshot = _paper_question(get_paper(db, assignment), question_id)
    if snapshot["question_type"] == "single_choice":
        if not payload.selected_option:
            raise BizError(400, "单选题请选择答案")
        options = [str(item) for item in snapshot.get("options") or []]
        if payload.selected_option not in options:
            raise BizError(400, "所选答案不在题目选项中")
    else:
        if not (payload.answer_text or "").strip():
            raise BizError(400, "简答题请填写作答内容")

    row = db.scalar(
        select(AssessmentAnswer).where(
            AssessmentAnswer.assignment_id == assignment.id,
            AssessmentAnswer.question_id == question_id,
        )
    )
    if row is None:
        row = AssessmentAnswer(
            assignment_id=assignment.id,
            question_id=question_id,
            question_snapshot_json=snapshot,
            selected_option=payload.selected_option,
            answer_text=payload.answer_text,
            grading_status="pending",
        )
        db.add(row)
    else:
        row.question_snapshot_json = snapshot
        row.selected_option = payload.selected_option
        row.answer_text = payload.answer_text
        row.grading_status = "pending"
        row.score = None
        row.is_correct = None
        row.failure_reason = None
        row.graded_by = None
        row.graded_at = None
    if assignment.status == "assigned":
        assignment.status = "in_progress"
        assignment.started_at = assignment.started_at or datetime.now()
    # 自适应需要即时表现来调整下一题难度；单选为确定性评分，不调用模型
    if assignment.mode == "adaptive" and snapshot.get("question_type") == "single_choice":
        _grade_answer(row, default_grade_short_answer)
    db.commit()
    db.refresh(row)
    return row


def get_answers(db: Session, assignment_id: int, user: User) -> list[AssessmentAnswer]:
    assignment = get_assignment(db, assignment_id, user)
    rows = list(
        db.scalars(
            select(AssessmentAnswer)
            .where(AssessmentAnswer.assignment_id == assignment.id)
            .order_by(AssessmentAnswer.question_id.asc())
        )
    )
    return rows


def _upsert_empty_answers(db: Session, assignment: AssessmentAssignment, paper: AssessmentPaper) -> list[AssessmentAnswer]:
    existing = {
        row.question_id: row
        for row in db.scalars(
            select(AssessmentAnswer).where(AssessmentAnswer.assignment_id == assignment.id)
        )
    }
    for item in paper.questions_json or []:
        question_id = int(item["question_id"])
        if question_id in existing:
            continue
        row = AssessmentAnswer(
            assignment_id=assignment.id,
            question_id=question_id,
            question_snapshot_json=dict(item),
            grading_status="pending",
        )
        db.add(row)
        existing[question_id] = row
    db.flush()
    return list(existing.values())


def _grade_answer(row: AssessmentAnswer, grader: Grader) -> None:
    snapshot = dict(row.question_snapshot_json or {})
    if snapshot.get("question_type") == "single_choice":
        is_correct, score = grade_single_choice(snapshot.get("correct_answer"), row.selected_option)
        row.is_correct = 1 if is_correct else 0
        row.score = score
        row.grading_status = "graded"
        row.failure_reason = None
        row.graded_at = datetime.now()
        return
    try:
        row.score = float(grader(snapshot, row.answer_text or ""))
        row.is_correct = None
        row.grading_status = "graded"
        row.failure_reason = None
        row.graded_at = datetime.now()
    except Exception as exc:  # noqa: BLE001 - 模型失败必须落状态而不是零分
        row.score = None
        row.grading_status = "failed"
        row.failure_reason = f"{type(exc).__name__}: {exc}"[:500]
        row.graded_at = datetime.now()


def _latest_result(db: Session, assignment_id: int) -> AssessmentResult | None:
    return db.scalar(
        select(AssessmentResult)
        .where(AssessmentResult.assignment_id == assignment_id)
        .order_by(AssessmentResult.version.desc())
    )


def visible_result(
    db: Session, assignment: AssessmentAssignment, user: User
) -> AssessmentResult | None:
    """按公开规则取可见结果：招聘只看已公布版本，员工完成后看最新版本。"""
    latest = _latest_result(db, assignment.id)
    if latest is None:
        return None
    if is_admin(user):
        return latest
    if assignment.purpose == "employee":
        return latest if assignment.status == "completed" else None
    if assignment.published_result_version is None:
        return None
    return db.scalar(
        select(AssessmentResult).where(
            AssessmentResult.assignment_id == assignment.id,
            AssessmentResult.version == assignment.published_result_version,
        )
    )


def _create_result(
    db: Session,
    assignment: AssessmentAssignment,
    context: dict[str, Any],
    answers: list[AssessmentAnswer],
    *,
    user_id: int | None,
) -> AssessmentResult:
    dimensions = context["dimensions"]
    pass_score = float(context["pass_score"])
    payload = [
        {
            "dimension_name": (row.question_snapshot_json or {}).get("dimension_name"),
            "score": float(row.score or 0),
        }
        for row in answers
    ]
    computed = compute_result(dimensions, payload, pass_score)
    strategy = dict(context.get("strategy") or {})
    ability = build_ability(
        computed["dimension_scores"],
        total_score=computed["total_score"],
        pass_score=pass_score,
        strategy=strategy,
    )
    latest = db.scalar(
        select(AssessmentResult.version)
        .where(AssessmentResult.assignment_id == assignment.id)
        .order_by(AssessmentResult.version.desc())
    )
    version = int(latest or 0) + 1
    result = AssessmentResult(
        assignment_id=assignment.id,
        version=version,
        dimension_scores_json=computed["dimension_scores"],
        total_score=computed["total_score"],
        pass_score=pass_score,
        passed=1 if computed["passed"] else 0,
        grading_snapshot_json={
            "prompt_version": GRADING_PROMPT_VERSION,
            "mode": assignment.mode,
            "paper_id": context["paper_id"],
            "plan_id": context["plan_id"],
            "plan_version": context["plan_version"],
            "selected_question_count": len(answers) if assignment.mode == "adaptive" else None,
            "strategy": strategy or None,
            "calculated_total_raw": computed["total_score_raw"],
        },
        ability_json=ability,
        created_by=user_id,
    )
    db.add(result)
    db.flush()

    # 错题复习事项：关联原答题快照，后续改题/复测不影响
    graded = {row.question_id: row for row in answers}
    for question_id, row in graded.items():
        if float(row.score or 0) >= 100:
            continue
        existing = db.scalar(
            select(AssessmentReviewItem).where(
                AssessmentReviewItem.assignment_id == assignment.id,
                AssessmentReviewItem.question_id == question_id,
                AssessmentReviewItem.user_id == assignment.user_id,
            )
        )
        if existing is None:
            db.add(
                AssessmentReviewItem(
                    assignment_id=assignment.id,
                    question_id=question_id,
                    user_id=assignment.user_id,
                    is_completed=0,
                    question_snapshot_json=dict(row.question_snapshot_json or {}),
                )
            )
    if assignment.purpose == "employee":
        db.add(
            TrainingGrowthEvent(
                employee_user_id=assignment.user_id,
                talent_profile_id=assignment.talent_profile_id,
                event_type="assessment_completed",
                ref_type="assessment_result",
                ref_id=result.id,
                summary=f"完成员工能力测评（方案 {context['plan_id']}，总分 {computed['total_score']}）",
                payload_json={"assignment_id": assignment.id, "version": version},
            )
        )
    return result


def submit_assignment(
    db: Session, assignment_id: int, user: User, *, grader: Grader | None = None
) -> dict[str, Any]:
    assignment = get_assignment(db, assignment_id, user)
    if assignment.status == "cancelled":
        raise BizError(400, "测评已撤销，不能交卷")
    existing = _latest_result(db, assignment.id)
    if existing is not None and assignment.status == "completed":
        return {"assignment_id": assignment.id, "status": assignment.status, "result": existing}

    context = _assignment_context(db, assignment)
    if assignment.mode == "adaptive":
        if not _selection_rows(db, assignment.id):
            raise BizError(400, "尚未出题，不能交卷")
        answers = _selection_answers(db, assignment)
    else:
        answers = _upsert_empty_answers(db, assignment, get_paper(db, assignment))
    effective_grader = grader or default_grade_short_answer
    for row in answers:
        if row.grading_status == "pending":
            _grade_answer(row, effective_grader)

    now = datetime.now()
    assignment.submitted_at = assignment.submitted_at or now
    failed = [row for row in answers if row.grading_status != "graded"]
    if failed:
        assignment.status = "grading"
        db.commit()
        return {
            "assignment_id": assignment.id,
            "status": assignment.status,
            "failed_count": len(failed),
            "result": None,
        }

    result = _create_result(db, assignment, context, answers, user_id=None)
    assignment.status = "completed"
    assignment.completed_at = now
    db.commit()
    db.refresh(result)
    db.refresh(assignment)
    return {"assignment_id": assignment.id, "status": assignment.status, "result": result}


def retry_grading(
    db: Session, assignment_id: int, user: User, *, grader: Grader | None = None
) -> dict[str, Any]:
    assignment = get_assignment(db, assignment_id, user)
    if assignment.status == "cancelled":
        raise BizError(400, "测评已撤销")
    context = _assignment_context(db, assignment)
    if assignment.mode == "adaptive":
        answers = _selection_answers(db, assignment)
    else:
        answers = list(
            db.scalars(
                select(AssessmentAnswer).where(AssessmentAnswer.assignment_id == assignment.id)
            )
        )
    effective_grader = grader or default_grade_short_answer
    retried = 0
    for row in answers:
        if row.grading_status in {"failed", "pending"}:
            _grade_answer(row, effective_grader)
            retried += 1
    failed = [row for row in answers if row.grading_status != "graded"]
    if failed:
        assignment.status = "grading"
        db.commit()
        return {"assignment_id": assignment.id, "status": assignment.status,
                "retried": retried, "failed_count": len(failed), "result": None}

    result = _latest_result(db, assignment.id)
    if result is None:
        result = _create_result(db, assignment, context, answers, user_id=None)
        assignment.completed_at = datetime.now()
    assignment.status = "completed"
    db.commit()
    db.refresh(result)
    return {"assignment_id": assignment.id, "status": assignment.status,
            "retried": retried, "failed_count": 0, "result": result}


def get_result(db: Session, assignment_id: int, user: User) -> dict[str, Any]:
    assignment = get_assignment(db, assignment_id, user)
    result = visible_result(db, assignment, user)
    if result is None:
        latest = _latest_result(db, assignment.id)
        if latest is None:
            raise BizError(404, "测评尚未生成结果")
        raise BizError(403, "招聘测评成绩尚未由管理员公布")

    review_items: list[AssessmentReviewItem] | None = None
    if _answer_visible(assignment, user):
        review_items = list(
            db.scalars(
                select(AssessmentReviewItem)
                .where(
                    AssessmentReviewItem.assignment_id == assignment.id,
                    AssessmentReviewItem.user_id == assignment.user_id,
                )
                .order_by(AssessmentReviewItem.question_id.asc())
            )
        )
    return {
        "assignment": assignment,
        "result": result,
        "published": (
            True if assignment.purpose == "employee"
            else assignment.published_result_version == result.version
        ),
        "answers_open": _answer_visible(assignment, user),
        "review_items": review_items,
    }


def publish_result(db: Session, assignment_id: int, user: User) -> AssessmentAssignment:
    require_admin(user)
    assignment = db.get(AssessmentAssignment, assignment_id)
    if assignment is None:
        raise BizError(404, "测评任务不存在")
    latest = _latest_result(db, assignment.id)
    if latest is None:
        raise BizError(400, "评分尚未完成，不能公布成绩")
    if assignment.status != "completed":
        raise BizError(400, "评分尚未完成，不能公布成绩")
    assignment.published_result_version = latest.version
    db.commit()
    db.refresh(assignment)
    return assignment


def open_answers(db: Session, assignment_id: int, user: User) -> AssessmentAssignment:
    require_admin(user)
    assignment = db.get(AssessmentAssignment, assignment_id)
    if assignment is None:
        raise BizError(404, "测评任务不存在")
    assignment.answers_open = 1
    db.commit()
    db.refresh(assignment)
    return assignment


def manual_score(
    db: Session,
    assignment_id: int,
    question_id: int,
    payload: ManualScoreRequest,
    user: User,
) -> dict[str, Any]:
    require_admin(user)
    assignment = db.get(AssessmentAssignment, assignment_id)
    if assignment is None:
        raise BizError(404, "测评任务不存在")
    context = _assignment_context(db, assignment)
    answer = db.scalar(
        select(AssessmentAnswer).where(
            AssessmentAnswer.assignment_id == assignment.id,
            AssessmentAnswer.question_id == question_id,
        )
    )
    if answer is None:
        raise BizError(404, "该题尚未作答")
    before_result = _latest_result(db, assignment.id)
    before = {
        "score": float(answer.score) if answer.score is not None else None,
        "grading_status": answer.grading_status,
        "is_correct": answer.is_correct,
        "result_version": before_result.version if before_result else None,
        "result_total": float(before_result.total_score) if before_result else None,
    }
    answer.score = round(float(payload.score), 2)
    answer.grading_status = "manual"
    answer.is_correct = None
    answer.failure_reason = None
    answer.graded_by = user.id
    answer.graded_at = datetime.now()
    review = AssessmentReview(
        assignment_id=assignment.id,
        result_id=before_result.id if before_result else None,
        reviewer_id=user.id,
        reason=payload.reason.strip(),
        before_json=before,
        after_json={"score": answer.score, "question_id": question_id},
    )
    db.add(review)

    answers = list(
        db.scalars(
            select(AssessmentAnswer).where(AssessmentAnswer.assignment_id == assignment.id)
        )
    )
    if any(row.grading_status in {"pending", "failed"} for row in answers):
        db.commit()
        return {"assignment": assignment, "result": None, "review_id": review.id,
                "message": "仍有未完成评分的题目，暂不生成新结果版本"}
    result = _create_result(db, assignment, context, answers, user_id=user.id)
    assignment.status = "completed"
    assignment.completed_at = assignment.completed_at or datetime.now()
    db.commit()
    db.refresh(review)
    db.refresh(result)
    return {"assignment": assignment, "result": result, "review_id": review.id}


def complete_review_item(
    db: Session, assignment_id: int, question_id: int, user: User
) -> AssessmentReviewItem:
    assignment = get_assignment(db, assignment_id, user)
    if not _answer_visible(assignment, user):
        raise BizError(403, "标准答案与复习入口尚未开放")
    row = db.scalar(
        select(AssessmentReviewItem).where(
            AssessmentReviewItem.assignment_id == assignment.id,
            AssessmentReviewItem.question_id == question_id,
            AssessmentReviewItem.user_id == assignment.user_id,
        )
    )
    if row is None:
        raise BizError(404, "复习事项不存在")
    row.is_completed = 1
    row.completed_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------- 序列化辅助

def public_result(result: AssessmentResult) -> dict[str, Any]:
    return {
        "version": result.version,
        "dimension_scores": list(result.dimension_scores_json or []),
        "total_score": float(result.total_score),
        "pass_score": float(result.pass_score),
        "passed": bool(result.passed),
        "ability": dict(result.ability_json or {}) or None,
        "grading_snapshot": dict(result.grading_snapshot_json or {}),
        "created_at": result.created_at,
    }


def question_public_snapshot(question: dict[str, Any]) -> dict[str, Any]:
    """开放前裁剪：不返回标准答案、参考要点与评分说明。"""
    return {
        "question_id": question.get("question_id"),
        "dimension_name": question.get("dimension_name"),
        "dimension_type": question.get("dimension_type"),
        "difficulty": question.get("difficulty"),
        "question_type": question.get("question_type"),
        "stem": question.get("stem"),
        "options": question.get("options") or [],
    }
