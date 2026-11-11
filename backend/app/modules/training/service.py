"""C 培训成长域服务（契约 §5）。

- 仅员工可被分配培训；候选人/管理员本人无培训任务（后端校验，不只隐藏按钮）。
- 课程只接受真实 http/https 材料链接；草稿只从有效课程目录选择，不编造链接。
- 计划保留版本快照；员工学习状态为自报，完成不等于验证学时或能力评级变化。
- 员工测评可生成培训草稿；招聘测评明确跳过（C-Q13/固定流程口径）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BizError
from app.models import User
from app.modules.assessments.models import AssessmentAssignment, AssessmentResult
from app.modules.assessments.service import is_admin, require_admin
from app.core.auth.scope import get_role_codes
from app.modules.notifications import producers as notify
from app.modules.training import drafting, feedback_themes
from app.modules.training.models import (
    CapabilityRating,
    CapabilityRatingChange,
    PrejobPhase,
    PrejobPlan,
    PrejobPlanVersion,
    TrainingAttendance,
    TrainingCourse,
    TrainingEnrollment,
    TrainingFeedback,
    TrainingFeedbackTheme,
    TrainingGrowthEvent,
    TrainingLearningEvidence,
    TrainingLearningRecord,
    TrainingPlan,
    TrainingPlanItem,
    TrainingPlanVersion,
    TrainingSession,
)
from app.modules.training.schemas import (
    AttendanceMarkRequest,
    CapabilityRatingChangeCreateRequest,
    CapabilityRatingReviewRequest,
    CourseCreateRequest,
    CourseUpdateRequest,
    DraftRequest,
    EnrollmentDecideRequest,
    EnrollmentRequest,
    EvidenceSubmitRequest,
    ExtensionDecideRequest,
    FeedbackNoteRequest,
    FeedbackSubmitRequest,
    PlanCreateRequest,
    PlanItemIn,
    PlanUpdateRequest,
    PrejobPlanCreateRequest,
    PrejobPlanUpdateRequest,
    SessionCreateRequest,
)

DraftGenerator = Callable[..., drafting.DraftResult]
ThemeExtractor = Callable[[list[str]], list[list[str]]]

FEEDBACK_WINDOW_DAYS = 7
MIN_FEEDBACK_SAMPLES = 5

# 测试可替换；生产调用真实模型
theme_extractor: ThemeExtractor = feedback_themes.extract_themes


def _validate_course_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BizError(400, "课程材料链接必须是 http/https 可访问地址")
    return url.strip()


def _require_employee(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise BizError(404, "员工账号不存在")
    if user.role != "employee":
        raise BizError(400, "培训计划仅面向在职员工；候选人入职关联员工身份后再分配")
    return user


# ---------------------------------------------------------------- 课程

def _course_columns(payload: CourseCreateRequest | CourseUpdateRequest) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    columns: dict[str, Any] = {}
    if "title" in data:
        columns["title"] = str(data["title"]).strip()
    if "description" in data:
        columns["description"] = data["description"]
    if "material_url" in data:
        columns["material_url"] = _validate_course_url(str(data["material_url"]))
    if "dimension_names" in data:
        columns["dimension_names_json"] = [str(item).strip() for item in data["dimension_names"] or [] if str(item).strip()]
    if "status" in data:
        columns["status"] = data["status"]
    if "capacity" in data:
        columns["capacity"] = data["capacity"]
    return columns


def create_course(db: Session, payload: CourseCreateRequest, *, admin: User) -> TrainingCourse:
    require_admin(admin)
    row = TrainingCourse(
        **_course_columns(payload),
        version=1,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_course(
    db: Session, course_id: int, payload: CourseUpdateRequest, *, admin: User
) -> TrainingCourse:
    require_admin(admin)
    row = db.get(TrainingCourse, course_id)
    if row is None:
        raise BizError(404, "课程不存在")
    for key, value in _course_columns(payload).items():
        setattr(row, key, value)
    row.version += 1
    row.updated_by = admin.id
    db.commit()
    db.refresh(row)
    return row


def list_courses(
    db: Session,
    *,
    status: str | None = None,
    keyword: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[TrainingCourse], int]:
    stmt = select(TrainingCourse)
    if status:
        stmt = stmt.where(TrainingCourse.status == status)
    if keyword:
        stmt = stmt.where(TrainingCourse.title.like(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(TrainingCourse.id.desc()).offset(offset).limit(limit)))
    return rows, int(total)


def _course_snapshot(course: TrainingCourse) -> dict[str, Any]:
    return {
        "course_id": course.id,
        "title": course.title,
        "description": course.description,
        "material_url": course.material_url,
        "dimension_names": list(course.dimension_names_json or []),
        "capacity": course.capacity,
        "version": course.version,
        "snapshot_at": datetime.now().isoformat(timespec="seconds"),
    }


def _build_items(
    db: Session, plan_id: int, plan_version: int, items: list[PlanItemIn]
) -> list[TrainingPlanItem]:
    rows: list[TrainingPlanItem] = []
    for order, item in enumerate(items):
        course = db.get(TrainingCourse, item.course_id)
        if course is None:
            raise BizError(400, f"课程 {item.course_id} 不存在，不能写入培训计划")
        if course.status != "active":
            raise BizError(400, f"课程「{course.title}」已失效，不能分配")
        rows.append(
            TrainingPlanItem(
                plan_id=plan_id,
                plan_version=plan_version,
                course_id=course.id,
                course_snapshot_json=_course_snapshot(course),
                dimension_name=item.dimension_name,
                reason=item.reason,
                sort_order=order,
                due_date=item.due_date,
            )
        )
    return rows


def _snapshot_plan(db: Session, plan: TrainingPlan, status: str, *, user_id: int, note: str | None = None) -> None:
    items = list(
        db.scalars(
            select(TrainingPlanItem)
            .where(
                TrainingPlanItem.plan_id == plan.id,
                TrainingPlanItem.plan_version == plan.version,
            )
            .order_by(TrainingPlanItem.sort_order.asc())
        )
    )
    snapshot = {
        "version": plan.version,
        "status": status,
        "source": plan.source,
        "target_job_title": plan.target_job_title,
        "gap_summary": plan.gap_summary,
        "items": [
            {
                "course_id": item.course_id,
                "course": dict(item.course_snapshot_json or {}),
                "dimension_name": item.dimension_name,
                "reason": item.reason,
            }
            for item in items
        ],
    }
    existing = db.scalar(
        select(TrainingPlanVersion).where(
            TrainingPlanVersion.plan_id == plan.id,
            TrainingPlanVersion.version == plan.version,
        )
    )
    if existing is None:
        db.add(
            TrainingPlanVersion(
                plan_id=plan.id,
                version=plan.version,
                status=status,
                snapshot_json=snapshot,
                note=note,
                created_by=user_id,
            )
        )
    else:
        existing.status = status
        existing.snapshot_json = snapshot
        if note:
            existing.note = note


def _create_plan(
    db: Session,
    *,
    employee: User,
    payload: PlanCreateRequest | DraftRequest,
    source: str,
    items: list[PlanItemIn],
    gap_summary: str | None,
    input_snapshot: dict[str, Any],
    admin: User,
) -> TrainingPlan:
    plan = TrainingPlan(
        employee_user_id=employee.id,
        talent_profile_id=getattr(payload, "talent_profile_id", None),
        job_id=getattr(payload, "job_id", None),
        target_job_title=(getattr(payload, "target_job_title", None) or "").strip() or None,
        source=source,
        status="draft",
        input_snapshot_json=input_snapshot,
        gap_summary=gap_summary,
        version=1,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(plan)
    db.flush()
    db.add_all(_build_items(db, plan.id, plan.version, items))
    db.flush()
    _snapshot_plan(db, plan, "draft", user_id=admin.id)
    return plan


def create_manual_plan(db: Session, payload: PlanCreateRequest, *, admin: User) -> TrainingPlan:
    require_admin(admin)
    employee = _require_employee(db, payload.employee_user_id)
    plan = _create_plan(
        db,
        employee=employee,
        payload=payload,
        source="manual",
        items=payload.items,
        gap_summary=payload.gap_summary,
        input_snapshot={"source": "manual", "created_by": admin.id},
        admin=admin,
    )
    db.commit()
    db.refresh(plan)
    return plan


def create_ai_draft(
    db: Session,
    payload: DraftRequest,
    *,
    admin: User,
    generator: DraftGenerator | None = None,
) -> dict[str, Any]:
    """真实课程 + 测评/档案输入生成 AI 草稿；不自动分配。"""
    require_admin(admin)
    employee = _require_employee(db, payload.employee_user_id)
    courses = list(
        db.scalars(select(TrainingCourse).where(TrainingCourse.status == "active").order_by(TrainingCourse.id.asc()))
    )
    if not courses:
        return {
            "generated": False,
            "reason": "课程库中没有有效课程，请先维护真实课程后再生成草稿",
            "gap_message": "缺少可用课程",
            "plan": None,
        }

    assessment_evidence: str | None = None
    if payload.assessment_assignment_id is not None:
        assignment = db.get(AssessmentAssignment, payload.assessment_assignment_id)
        if assignment is None:
            raise BizError(404, "测评任务不存在")
        if assignment.user_id != employee.id:
            raise BizError(400, "测评任务不属于该员工")
        if assignment.purpose == "recruitment":
            return {
                "generated": False,
                "reason": "招聘测评不生成候选人培训草稿，请入职关联员工身份后人工分配培训",
                "gap_message": "招聘测评跳过培训草稿",
                "plan": None,
            }
        result = db.scalar(
            select(AssessmentResult)
            .where(AssessmentResult.assignment_id == assignment.id)
            .order_by(AssessmentResult.version.desc())
        )
        if result is None:
            raise BizError(400, "员工测评尚未完成评分，不能生成培训草稿")
        dimensions_text = "，".join(
            f"{item.get('name')} {item.get('score')} 分"
            for item in (result.dimension_scores_json or [])
        )
        assessment_evidence = f"测评结果 v{result.version}：总分 {float(result.total_score)}；{dimensions_text}"

    course_payload = [
        {
            "id": course.id,
            "title": course.title,
            "description": course.description,
            "dimensions": list(course.dimension_names_json or []),
        }
        for course in courses
    ]
    effective_generator = generator or drafting.generate_draft
    try:
        draft = effective_generator(
            target_job_title=payload.target_job_title,
            dimension_gaps=list(payload.dimension_gaps),
            profile_summary=payload.profile_summary,
            assessment_evidence=assessment_evidence,
            courses=course_payload,
        )
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001 - 模型失败必须明确提示，不能静默生成空计划
        raise BizError(503, f"模型生成培训草稿失败，请稍后重试：{type(exc).__name__}") from exc

    valid_ids = {course.id for course in courses}
    valid_items = [item for item in draft.items if item.course_id in valid_ids]
    if not valid_items:
        return {
            "generated": False,
            "reason": "模型未从真实课程目录中选择有效课程，请补充课程后重试",
            "gap_message": draft.gap_message or "模型未选择有效课程",
            "plan": None,
        }
    plan = _create_plan(
        db,
        employee=employee,
        payload=payload,
        source="ai",
        items=[
            PlanItemIn(course_id=item.course_id, dimension_name=item.dimension_name, reason=item.reason)
            for item in valid_items
        ],
        gap_summary=draft.gap_message or None,
        input_snapshot={
            "target_job_title": payload.target_job_title,
            "dimension_gaps": list(payload.dimension_gaps),
            "profile_summary": payload.profile_summary,
            "assessment_evidence": assessment_evidence,
            "course_ids": [course.id for course in courses],
            "prompt_version": drafting.DRAFT_PROMPT_VERSION,
        },
        admin=admin,
    )
    db.commit()
    db.refresh(plan)
    return {"generated": True, "reason": None, "gap_message": plan.gap_summary, "plan": plan}


def _require_plan(db: Session, plan_id: int) -> TrainingPlan:
    plan = db.get(TrainingPlan, plan_id)
    if plan is None:
        raise BizError(404, "培训计划不存在")
    return plan


def update_plan(db: Session, plan_id: int, payload: PlanUpdateRequest, *, admin: User) -> TrainingPlan:
    require_admin(admin)
    plan = _require_plan(db, plan_id)
    if plan.status != "draft":
        raise BizError(400, "已分配计划不能直接修改，请先修订生成新版本")
    fields = payload.model_dump(exclude_unset=True)
    if "target_job_title" in fields:
        plan.target_job_title = (fields["target_job_title"] or "").strip() or None
    if "gap_summary" in fields:
        plan.gap_summary = fields["gap_summary"]
    if "items" in fields:
        db.query(TrainingPlanItem).filter(
            TrainingPlanItem.plan_id == plan.id,
            TrainingPlanItem.plan_version == plan.version,
        ).delete(synchronize_session=False)
        db.flush()
        db.add_all(_build_items(db, plan.id, plan.version, payload.items or []))
    plan.updated_by = admin.id
    db.flush()
    _snapshot_plan(db, plan, "draft", user_id=admin.id)
    db.commit()
    db.refresh(plan)
    return plan


def assign_plan(db: Session, plan_id: int, *, admin: User) -> TrainingPlan:
    require_admin(admin)
    plan = _require_plan(db, plan_id)
    if plan.status != "draft":
        raise BizError(400, "只有草稿计划可以分配")
    items = list(
        db.scalars(
            select(TrainingPlanItem).where(
                TrainingPlanItem.plan_id == plan.id,
                TrainingPlanItem.plan_version == plan.version,
            )
        )
    )
    if not items:
        raise BizError(400, "计划没有课程项，不能分配")
    plan.status = "assigned"
    plan.updated_by = admin.id
    db.flush()
    _snapshot_plan(db, plan, "assigned", user_id=admin.id, note="管理员确认分配")
    db.add(
        TrainingGrowthEvent(
            employee_user_id=plan.employee_user_id,
            talent_profile_id=plan.talent_profile_id,
            event_type="training_assigned",
            ref_type="training_plan",
            ref_id=plan.id,
            summary=f"分配培训计划 v{plan.version}",
            payload_json={"plan_id": plan.id, "version": plan.version},
        )
    )
    db.commit()
    db.refresh(plan)
    # 通知 D 域：课程指派（ref_type=training_plan_item，幂等键按计划项）
    for item in items:
        notify.course_assigned(
            db,
            plan_item_id=item.id,
            recipient_user_id=plan.employee_user_id,
            course_name=(item.course_snapshot_json or {}).get("title") or f"课程 {item.course_id}",
            due_date=item.due_date.isoformat() if item.due_date else None,
            actor_user_id=admin.id,
        )
    db.commit()
    return plan


def revise_plan(db: Session, plan_id: int, *, admin: User) -> TrainingPlan:
    """已分配计划 → 新版本草稿；旧版本快照与学习记录保留。"""
    require_admin(admin)
    plan = _require_plan(db, plan_id)
    if plan.status != "assigned":
        raise BizError(400, "只有已分配计划可以修订")
    current_items = list(
        db.scalars(
            select(TrainingPlanItem)
            .where(
                TrainingPlanItem.plan_id == plan.id,
                TrainingPlanItem.plan_version == plan.version,
            )
            .order_by(TrainingPlanItem.sort_order.asc())
        )
    )
    plan.version += 1
    plan.status = "draft"
    plan.updated_by = admin.id
    for item in current_items:
        course = db.get(TrainingCourse, item.course_id)
        snapshot = _course_snapshot(course) if course is not None else dict(item.course_snapshot_json or {})
        db.add(
            TrainingPlanItem(
                plan_id=plan.id,
                plan_version=plan.version,
                course_id=item.course_id,
                course_snapshot_json=snapshot,
                dimension_name=item.dimension_name,
                reason=item.reason,
                sort_order=item.sort_order,
            )
        )
    db.flush()
    _snapshot_plan(db, plan, "draft", user_id=admin.id, note="由已分配计划修订")
    db.commit()
    db.refresh(plan)
    return plan


def list_plans(
    db: Session,
    *,
    employee_user_id: int | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[TrainingPlan], int]:
    stmt = select(TrainingPlan)
    if employee_user_id is not None:
        stmt = stmt.where(TrainingPlan.employee_user_id == employee_user_id)
    if status:
        stmt = stmt.where(TrainingPlan.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(TrainingPlan.id.desc()).offset(offset).limit(limit)))
    return rows, int(total)


def get_plan(db: Session, plan_id: int) -> TrainingPlan:
    return _require_plan(db, plan_id)


def plan_items(db: Session, plan: TrainingPlan) -> list[TrainingPlanItem]:
    return list(
        db.scalars(
            select(TrainingPlanItem)
            .where(
                TrainingPlanItem.plan_id == plan.id,
                TrainingPlanItem.plan_version == plan.version,
            )
            .order_by(TrainingPlanItem.sort_order.asc())
        )
    )


def _prejob_phase_rows(db: Session, plan: PrejobPlan) -> list[PrejobPhase]:
    return list(
        db.scalars(
            select(PrejobPhase)
            .where(PrejobPhase.plan_id == plan.id, PrejobPhase.plan_version == plan.version)
            .order_by(PrejobPhase.seq.asc())
        )
    )


def _build_prejob_phases(db: Session, plan_id: int, plan_version: int, phases: list) -> list[PrejobPhase]:
    rows: list[PrejobPhase] = []
    for order, phase in enumerate(phases):
        courses: list[dict[str, Any]] = []
        for entry in phase.courses:
            course = db.get(TrainingCourse, entry.course_id)
            if course is None:
                raise BizError(400, f"课程 {entry.course_id} 不存在，不能写入岗前计划")
            if course.status != "active":
                raise BizError(400, f"课程「{course.title}」已失效，不能写入岗前计划")
            courses.append(
                {
                    "course_id": course.id,
                    "course": _course_snapshot(course),
                    "note": entry.note,
                }
            )
        rows.append(
            PrejobPhase(
                plan_id=plan_id,
                plan_version=plan_version,
                seq=order,
                name=phase.name,
                goal=phase.goal,
                mentor_user_id=phase.mentor_user_id,
                courses_json=courses,
                status="pending",
            )
        )
    return rows


def _snapshot_prejob(db: Session, plan: PrejobPlan, status: str, *, user_id: int, note: str | None = None) -> None:
    phases = _prejob_phase_rows(db, plan)
    snapshot = {
        "version": plan.version,
        "status": status,
        "target_job_title": plan.target_job_title,
        "goal": plan.goal,
        "phases": [
            {
                "seq": ph.seq,
                "name": ph.name,
                "goal": ph.goal,
                "mentor_user_id": ph.mentor_user_id,
                "status": ph.status,
                "courses": [c["course_id"] for c in (ph.courses_json or [])],
            }
            for ph in phases
        ],
    }
    existing = db.scalar(
        select(PrejobPlanVersion).where(
            PrejobPlanVersion.plan_id == plan.id, PrejobPlanVersion.version == plan.version
        )
    )
    if existing is None:
        db.add(
            PrejobPlanVersion(
                plan_id=plan.id, version=plan.version, status=status, snapshot_json=snapshot,
                note=note, created_by=user_id,
            )
        )
    else:
        existing.status = status
        existing.snapshot_json = snapshot
        if note:
            existing.note = note


def create_prejob_plan(db: Session, payload: PrejobPlanCreateRequest, *, admin: User) -> PrejobPlan:
    """管理员/HR 为待上岗人员创建分阶段岗前培养计划（目标、阶段、课程、导师），草稿态。"""
    require_admin(admin)
    employee = _require_employee(db, payload.employee_user_id)
    plan = PrejobPlan(
        employee_user_id=employee.id,
        talent_profile_id=payload.talent_profile_id,
        target_job_title=(payload.target_job_title or "").strip() or None,
        goal=payload.goal,
        status="draft",
        input_snapshot_json={
            "phases": [p.model_dump() for p in payload.phases],
            "created_via": "manual",
        },
        version=1,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(plan)
    db.flush()
    db.add_all(_build_prejob_phases(db, plan.id, plan.version, payload.phases))
    db.flush()
    _snapshot_prejob(db, plan, "draft", user_id=admin.id)
    db.commit()
    db.refresh(plan)
    return plan


def update_prejob_plan(db: Session, plan_id: int, payload: PrejobPlanUpdateRequest, *, admin: User) -> PrejobPlan:
    """草稿态岗前计划可整体修订阶段；已分配需先 revise 生成新版本。"""
    require_admin(admin)
    plan = _require_prejob_plan(db, plan_id)
    if plan.status != "draft":
        raise BizError(400, "已分配岗前计划不能直接修改，请先修订生成新版本")
    fields = payload.model_dump(exclude_unset=True)
    if "target_job_title" in fields:
        plan.target_job_title = (fields["target_job_title"] or "").strip() or None
    if "goal" in fields:
        plan.goal = fields["goal"]
    if payload.phases is not None:
        for old in _prejob_phase_rows(db, plan):
            db.delete(old)
        db.flush()
        db.add_all(_build_prejob_phases(db, plan.id, plan.version, payload.phases))
    plan.updated_by = admin.id
    db.flush()
    _snapshot_prejob(db, plan, "draft", user_id=admin.id)
    db.commit()
    db.refresh(plan)
    return plan


def assign_prejob_plan(db: Session, plan_id: int, *, admin: User) -> PrejobPlan:
    require_admin(admin)
    plan = _require_prejob_plan(db, plan_id)
    if plan.status != "draft":
        raise BizError(400, "只有草稿岗前计划可以分配")
    if not _prejob_phase_rows(db, plan):
        raise BizError(400, "岗前计划没有阶段，不能分配")
    plan.status = "assigned"
    plan.updated_by = admin.id
    db.flush()
    _snapshot_prejob(db, plan, "assigned", user_id=admin.id, note="管理员确认分配")
    db.add(
        TrainingGrowthEvent(
            employee_user_id=plan.employee_user_id,
            talent_profile_id=plan.talent_profile_id,
            source_type="training",
            event_type="prejob_assigned",
            ref_type="prejob_plan",
            ref_id=plan.id,
            summary=f"分配岗前培养计划 v{plan.version}",
            payload_json={"plan_id": plan.id, "version": plan.version},
        )
    )
    db.commit()
    db.refresh(plan)
    # 岗前计划一期不自动报名，也不投递课程指派通知（与在职培训分配区分）
    return plan


def revise_prejob_plan(db: Session, plan_id: int, *, admin: User) -> PrejobPlan:
    """已分配岗前计划 → 新版本草稿；复制当前阶段，历史版本快照保留。"""
    require_admin(admin)
    plan = _require_prejob_plan(db, plan_id)
    if plan.status != "assigned":
        raise BizError(400, "只有已分配岗前计划可以修订")
    current = _prejob_phase_rows(db, plan)
    plan.version += 1
    plan.status = "draft"
    plan.updated_by = admin.id
    for ph in current:
        db.add(
            PrejobPhase(
                plan_id=plan.id,
                plan_version=plan.version,
                seq=ph.seq,
                name=ph.name,
                goal=ph.goal,
                mentor_user_id=ph.mentor_user_id,
                courses_json=list(ph.courses_json or []),
                status="pending",
            )
        )
    db.flush()
    _snapshot_prejob(db, plan, "draft", user_id=admin.id, note="由已分配岗前计划修订")
    db.commit()
    db.refresh(plan)
    return plan


def _require_prejob_plan(db: Session, plan_id: int) -> PrejobPlan:
    plan = db.get(PrejobPlan, plan_id)
    if plan is None:
        raise BizError(404, "岗前培养计划不存在")
    return plan


def get_prejob_plan(db: Session, plan_id: int, *, user: User) -> tuple[PrejobPlan, list[PrejobPhase]]:
    """管理员看全量；员工仅看本人岗前计划。"""
    plan = _require_prejob_plan(db, plan_id)
    if not is_admin(user) and plan.employee_user_id != user.id:
        raise BizError(403, "无权查看该岗前计划")
    return plan, _prejob_phase_rows(db, plan)


def list_prejob_plans(db: Session, *, user: User) -> list[PrejobPlan]:
    stmt = select(PrejobPlan).order_by(PrejobPlan.id.desc())
    if not is_admin(user):
        stmt = stmt.where(PrejobPlan.employee_user_id == user.id)
    return list(db.scalars(stmt))


# ---------------------------------------------------------------- 正式能力评级变更链（C14）

def _is_manager_caller(db: Session, user: User) -> bool:
    """部门经理或管理侧（system_admin/hr）可提交能力评级变更；员工本人不可为自己提交。"""
    codes = set(get_role_codes(db, user.id))
    return bool(codes & {"dept_manager"}) or is_admin(user)


def _require_capability_employee(db: Session, employee_user_id: int) -> User:
    emp = db.get(User, employee_user_id)
    if emp is None:
        raise BizError(404, "员工账号不存在")
    if emp.role != "employee":
        raise BizError(400, "正式能力评级仅面向在职员工")
    return emp


def create_capability_rating_change(
    db: Session, payload: CapabilityRatingChangeCreateRequest, *, user: User
) -> CapabilityRatingChange:
    """经理提交实践成果证据 + 经理评价生成变更申请，进入 HR 审核队列。

    员工不能为自己提交；证据为空、经理评级越界在 schema 已拦截，这里再校验身份链。
    """
    if not _is_manager_caller(db, user):
        raise BizError(403, "仅部门经理或管理员可提交能力评级变更")
    if payload.employee_user_id == user.id:
        raise BizError(400, "不能为本人提交正式能力评级变更")
    _require_capability_employee(db, payload.employee_user_id)
    row = CapabilityRatingChange(
        employee_user_id=payload.employee_user_id,
        talent_profile_id=payload.talent_profile_id,
        dimension_name=payload.dimension_name.strip(),
        evidence_json=[e.model_dump() for e in payload.evidence],
        manager_user_id=user.id,
        manager_level=payload.manager_level,
        manager_comment=payload.manager_comment.strip(),
        status="pending_hr",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def review_capability_rating_change(
    db: Session, change_id: int, payload: CapabilityRatingReviewRequest, *, user: User
) -> CapabilityRatingChange:
    """HR（管理侧）审核：通过则历史版本置非生效并追加新评级版本 + hr_ability 成长事件；拒绝仅落状态。

    课程完成/AI 结果不得自动改写正式评级，本函数是唯一的正式评级写入路径。
    """
    require_admin(user)
    row = db.get(CapabilityRatingChange, change_id)
    if row is None:
        raise BizError(404, "能力评级变更申请不存在")
    if row.status != "pending_hr":
        raise BizError(400, "该变更申请已处理，不能重复审核")
    row.hr_user_id = user.id
    row.hr_comment = (payload.comment or "").strip() or None
    if not payload.approve:
        row.status = "rejected"
        row.updated_by = user.id
        db.commit()
        db.refresh(row)
        return row
    if payload.final_level is None:
        raise BizError(400, "审核通过必须给出正式评级 level")
    row.hr_level = payload.final_level
    # 关闭同维度旧生效版本
    for prior in db.scalars(
        select(CapabilityRating).where(
            CapabilityRating.employee_user_id == row.employee_user_id,
            CapabilityRating.talent_profile_id == row.talent_profile_id,
            CapabilityRating.dimension_name == row.dimension_name,
            CapabilityRating.effective == 1,
        )
    ):
        prior.effective = 0
    max_version = db.scalar(
        select(func.max(CapabilityRating.version)).where(
            CapabilityRating.employee_user_id == row.employee_user_id,
            CapabilityRating.talent_profile_id == row.talent_profile_id,
            CapabilityRating.dimension_name == row.dimension_name,
        )
    )
    new_version = (max_version or 0) + 1
    db.add(
        CapabilityRating(
            employee_user_id=row.employee_user_id,
            talent_profile_id=row.talent_profile_id,
            dimension_name=row.dimension_name,
            level=row.hr_level,
            version=new_version,
            effective=1,
            source_change_id=row.id,
            basis_json={
                "evidence": row.evidence_json,
                "manager_user_id": row.manager_user_id,
                "manager_level": row.manager_level,
                "manager_comment": row.manager_comment,
                "hr_comment": row.hr_comment,
            },
            created_by=user.id,
        )
    )
    row.status = "approved"
    row.updated_by = user.id
    db.flush()
    db.add(
        TrainingGrowthEvent(
            employee_user_id=row.employee_user_id,
            talent_profile_id=row.talent_profile_id,
            source_type="hr_ability",
            event_type="ability_rating_changed",
            ref_type="capability_rating_change",
            ref_id=row.id,
            summary=f"{row.dimension_name} 正式评级更新为 {row.hr_level}（v{new_version}）",
            payload_json={
                "dimension_name": row.dimension_name,
                "level": row.hr_level,
                "version": new_version,
                "change_id": row.id,
            },
        )
    )
    db.commit()
    db.refresh(row)
    return row


def list_capability_ratings(
    db: Session, *, user: User, employee_user_id: int | None = None
) -> list[CapabilityRating]:
    """管理侧看全量（可按员工过滤）；员工仅看本人正式评级历史。"""
    if employee_user_id is None and not is_admin(user):
        employee_user_id = user.id
    stmt = select(CapabilityRating).order_by(
        CapabilityRating.dimension_name, CapabilityRating.version.desc()
    )
    if not is_admin(user):
        stmt = stmt.where(CapabilityRating.employee_user_id == user.id)
    elif employee_user_id is not None:
        stmt = stmt.where(CapabilityRating.employee_user_id == employee_user_id)
    return list(db.scalars(stmt))


def list_capability_rating_changes(
    db: Session, *, user: User, status: str | None = None
) -> list[CapabilityRatingChange]:
    """管理侧/经理看变更队列；员工仅看本人相关变更。"""
    stmt = select(CapabilityRatingChange).order_by(CapabilityRatingChange.id.desc())
    if not is_admin(user):
        stmt = stmt.where(CapabilityRatingChange.employee_user_id == user.id)
    if status:
        stmt = stmt.where(CapabilityRatingChange.status == status)
    return list(db.scalars(stmt))



def plan_versions(db: Session, plan: TrainingPlan) -> list[TrainingPlanVersion]:
    return list(
        db.scalars(
            select(TrainingPlanVersion)            .where(TrainingPlanVersion.plan_id == plan.id)
            .order_by(TrainingPlanVersion.version.desc())
        )
    )


def my_plans(db: Session, user: User) -> list[dict[str, Any]]:
    if user.role != "employee":
        raise BizError(403, "培训计划仅员工本人可查看；候选人入职后由管理员分配")
    plans = list(
        db.scalars(
            select(TrainingPlan)
            .where(
                TrainingPlan.employee_user_id == user.id,
                TrainingPlan.status == "assigned",
            )
            .order_by(TrainingPlan.id.desc())
        )
    )
    result: list[dict[str, Any]] = []
    for plan in plans:
        result.append({"plan": plan, "items": plan_items(db, plan)})
    return result


def _learning_record(db: Session, item_id: int, user: User) -> TrainingLearningRecord | None:
    return db.scalar(
        select(TrainingLearningRecord).where(
            TrainingLearningRecord.plan_item_id == item_id,
            TrainingLearningRecord.employee_user_id == user.id,
        )
    )


def learning_records(
    db: Session, items: list[TrainingPlanItem], employee_user_id: int
) -> dict[int, TrainingLearningRecord]:
    if not items:
        return {}
    rows = list(
        db.scalars(
            select(TrainingLearningRecord).where(
                TrainingLearningRecord.plan_item_id.in_([item.id for item in items]),
                TrainingLearningRecord.employee_user_id == employee_user_id,
            )
        )
    )
    return {row.plan_item_id: row for row in rows}


def update_progress(
    db: Session, item_id: int, payload, *, user: User
) -> TrainingLearningRecord:
    if user.role != "employee":
        raise BizError(403, "只有员工本人可更新学习状态")
    item = db.get(TrainingPlanItem, item_id)
    if item is None:
        raise BizError(404, "培训计划项不存在")
    plan = _require_plan(db, item.plan_id)
    if plan.employee_user_id != user.id:
        raise BizError(403, "无权更新他人的培训计划")
    if plan.status != "assigned":
        raise BizError(400, "计划尚未分配，不能记录学习状态")
    record = _learning_record(db, item.id, user)
    previous_status = record.status if record else "not_started"
    if record is None:
        record = TrainingLearningRecord(
            plan_item_id=item.id,
            employee_user_id=user.id,
            status=payload.status,
            note=payload.note,
            reported_at=datetime.now(),
            is_self_reported=1,
        )
        db.add(record)
    else:
        record.status = payload.status
        record.note = payload.note
        record.reported_at = datetime.now()
        record.is_self_reported = 1
    db.flush()
    if payload.status == "completed" and previous_status != "completed":
        db.add(
            TrainingGrowthEvent(
                employee_user_id=user.id,
                talent_profile_id=plan.talent_profile_id,
                event_type="course_completed",
                ref_type="training_plan_item",
                ref_id=item.id,
                summary=f"自报完成课程：{(item.course_snapshot_json or {}).get('title') or item.course_id}",
                payload_json={"plan_id": plan.id, "version": plan.version, "self_reported": True},
            )
        )
    db.commit()
    db.refresh(record)
    return record


def list_growth(
    db: Session,
    *,
    user: User,
    employee_user_id: int | None = None,
    talent_profile_id: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[TrainingGrowthEvent], int]:
    stmt = select(TrainingGrowthEvent)
    if not is_admin(user):
        stmt = stmt.where(TrainingGrowthEvent.employee_user_id == user.id)
    else:
        if employee_user_id is not None:
            stmt = stmt.where(TrainingGrowthEvent.employee_user_id == employee_user_id)
        if talent_profile_id is not None:
            stmt = stmt.where(TrainingGrowthEvent.talent_profile_id == talent_profile_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(TrainingGrowthEvent.id.desc()).offset(offset).limit(limit)))
    return rows, int(total)


def draft_from_assessment(
    db: Session, assignment_id: int, *, admin: User, generator: DraftGenerator | None = None
) -> dict[str, Any]:
    """固定流程入口：员工测评 → 培训草稿；招聘测评返回跳过原因，不生成候选人培训。"""
    require_admin(admin)
    assignment = db.get(AssessmentAssignment, assignment_id)
    if assignment is None:
        raise BizError(404, "测评任务不存在")
    if assignment.purpose == "recruitment":
        return {
            "generated": False,
            "reason": "招聘测评不生成候选人培训草稿",
            "gap_message": "招聘测评跳过培训草稿",
            "plan": None,
        }
    payload = DraftRequest(
        employee_user_id=assignment.user_id,
        talent_profile_id=assignment.talent_profile_id,
        job_id=assignment.job_id,
        target_job_title=assignment.job_title,
        assessment_assignment_id=assignment.id,
    )
    return create_ai_draft(db, payload, admin=admin, generator=generator)


# ---------------------------------------------------------------- 期次与出勤

def _naive_local(value: datetime) -> datetime:
    """统一为本地无时区时间；前端可能提交带偏移的 ISO 时间。"""
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _require_course(db: Session, course_id: int) -> TrainingCourse:
    course = db.get(TrainingCourse, course_id)
    if course is None:
        raise BizError(404, "课程不存在")
    return course


def create_session(
    db: Session, course_id: int, payload: SessionCreateRequest, *, admin: User
) -> TrainingSession:
    require_admin(admin)
    _require_course(db, course_id)
    starts_at = _naive_local(payload.starts_at)
    ends_at = _naive_local(payload.ends_at)
    if ends_at <= starts_at:
        raise BizError(400, "课程结束时间必须晚于开始时间")
    row = TrainingSession(
        course_id=course_id,
        instructor_name=(payload.instructor_name or "").strip() or None,
        starts_at=starts_at,
        ends_at=ends_at,
        created_by=admin.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_sessions(db: Session, course_id: int, *, admin: User) -> list[TrainingSession]:
    require_admin(admin)
    _require_course(db, course_id)
    return list(
        db.scalars(
            select(TrainingSession)
            .where(TrainingSession.course_id == course_id)
            .order_by(TrainingSession.starts_at.desc(), TrainingSession.id.desc())
        )
    )


def mark_attendance(
    db: Session, session_id: int, payload: AttendanceMarkRequest, *, admin: User
) -> TrainingAttendance:
    require_admin(admin)
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise BizError(404, "课程期次不存在")
    employee = _require_employee(db, payload.employee_user_id)
    existing = db.scalar(
        select(TrainingAttendance).where(
            TrainingAttendance.session_id == session_id,
            TrainingAttendance.employee_user_id == employee.id,
        )
    )
    if existing is not None:
        return existing
    row = TrainingAttendance(
        session_id=session_id,
        employee_user_id=employee.id,
        attended_at=datetime.now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_attendance(db: Session, session_id: int, *, admin: User) -> list[TrainingAttendance]:
    require_admin(admin)
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise BizError(404, "课程期次不存在")
    return list(
        db.scalars(
            select(TrainingAttendance)
            .where(TrainingAttendance.session_id == session_id)
            .order_by(TrainingAttendance.id.asc())
        )
    )


# ---------------------------------------------------------------- 反馈

def _require_employee_caller(user: User) -> None:
    if user.role != "employee":
        raise BizError(403, "只有员工本人可提交或查看培训评价")


def _attendance_session(db: Session, course_id: int, employee_user_id: int) -> TrainingSession | None:
    """最近一次已结束、且本人有出勤记录的期次。"""
    return db.scalars(
        select(TrainingSession)
        .join(TrainingAttendance, TrainingAttendance.session_id == TrainingSession.id)
        .where(
            TrainingSession.course_id == course_id,
            TrainingAttendance.employee_user_id == employee_user_id,
            TrainingSession.ends_at <= datetime.now(),
        )
        .order_by(TrainingSession.ends_at.desc(), TrainingSession.id.desc())
    ).first()


def _future_attendance_session(db: Session, course_id: int, employee_user_id: int) -> TrainingSession | None:
    return db.scalars(
        select(TrainingSession)
        .join(TrainingAttendance, TrainingAttendance.session_id == TrainingSession.id)
        .where(
            TrainingSession.course_id == course_id,
            TrainingAttendance.employee_user_id == employee_user_id,
            TrainingSession.ends_at > datetime.now(),
        )
        .order_by(TrainingSession.ends_at.asc())
    ).first()


def _feedback_row(db: Session, course_id: int, employee_user_id: int) -> TrainingFeedback | None:
    return db.scalar(
        select(TrainingFeedback).where(
            TrainingFeedback.course_id == course_id,
            TrainingFeedback.employee_user_id == employee_user_id,
        )
    )


def _feedback_out(row: TrainingFeedback) -> dict[str, Any]:
    return {
        "id": row.id,
        "course_id": row.course_id,
        "session_id": row.session_id,
        "content_score": row.content_score,
        "instructor_score": row.instructor_score,
        "comment": row.comment,
        "status": row.status,
        "submitted_at": row.submitted_at,
        "withdrawn_at": row.withdrawn_at,
        "updated_at": row.updated_at,
    }


def feedback_state(db: Session, course: TrainingCourse, user: User) -> dict[str, Any]:
    """员工侧评价状态：是否具备资格、窗口截止、本人提交与统一改进说明。"""
    _require_employee_caller(user)
    session = _attendance_session(db, course.id, user.id)
    row = _feedback_row(db, course.id, user.id)
    future = None if session is not None else _future_attendance_session(db, course.id, user.id)

    if session is None:
        reason = (
            "课程尚未结束，反馈窗口未开放"
            if future is not None
            else "仅实际参加课程期次并登记出勤的员工可以评价"
        )
        return {
            "attended": False,
            "eligible": False,
            "reason": reason,
            "window_closes_at": None,
            "feedback": _feedback_out(row) if row else None,
            "improvement_note": course.feedback_note,
            "improvement_note_at": course.feedback_note_at,
        }

    closes_at = session.ends_at + timedelta(days=FEEDBACK_WINDOW_DAYS)
    eligible = datetime.now() <= closes_at
    return {
        "attended": True,
        "eligible": eligible,
        "reason": None if eligible else "反馈窗口已关闭，已提交内容仍可查看",
        "window_closes_at": closes_at,
        "feedback": _feedback_out(row) if row else None,
        "improvement_note": course.feedback_note,
        "improvement_note_at": course.feedback_note_at,
    }


def submit_feedback(
    db: Session, course_id: int, payload: FeedbackSubmitRequest, *, user: User
) -> TrainingFeedback:
    """提交或修改本人评价；窗口内幂等按课程+员工更新同一行。"""
    _require_employee_caller(user)
    course = _require_course(db, course_id)
    state = feedback_state(db, course, user)
    if not state["eligible"]:
        raise BizError(403 if not state["attended"] else 400, state["reason"] or "不能提交反馈")
    session = _attendance_session(db, course.id, user.id)
    if session is None:
        raise BizError(403, "缺少有效出勤记录，不能提交反馈")

    row = _feedback_row(db, course.id, user.id)
    if row is None:
        row = TrainingFeedback(
            course_id=course.id,
            employee_user_id=user.id,
            session_id=session.id,
            content_score=payload.content_score,
            instructor_score=payload.instructor_score,
            comment=(payload.comment or "").strip() or None,
            status="submitted",
            submitted_at=datetime.now(),
        )
        db.add(row)
    else:
        row.session_id = session.id
        row.content_score = payload.content_score
        row.instructor_score = payload.instructor_score
        row.comment = (payload.comment or "").strip() or None
        row.status = "submitted"
        row.withdrawn_at = None
    db.commit()
    db.refresh(row)
    return row


def withdraw_feedback(db: Session, course_id: int, *, user: User) -> TrainingFeedback:
    """窗口内撤回；撤回后不计入汇总，窗口关闭后不可再改。"""
    _require_employee_caller(user)
    course = _require_course(db, course_id)
    row = _feedback_row(db, course.id, user.id)
    if row is None:
        raise BizError(404, "尚未提交过该课程的评价")
    state = feedback_state(db, course, user)
    if not state["eligible"]:
        raise BizError(400, state["reason"] or "反馈窗口已关闭，不能撤回")
    row.status = "withdrawn"
    row.withdrawn_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


def my_feedback(db: Session, course_id: int, *, user: User) -> dict[str, Any]:
    _require_employee_caller(user)
    return feedback_state(db, _require_course(db, course_id), user)


def feedback_summary(db: Session, course_id: int, *, admin: User) -> dict[str, Any]:
    """仅管理员查看的匿名汇总；少于门槛不返回均分与主题。"""
    require_admin(admin)
    course = _require_course(db, course_id)
    rows = list(
        db.scalars(
            select(TrainingFeedback).where(
                TrainingFeedback.course_id == course_id,
                TrainingFeedback.status == "submitted",
            )
        )
    )
    count = len(rows)
    enough = count >= MIN_FEEDBACK_SAMPLES
    content_avg = round(sum(row.content_score for row in rows) / count, 2) if enough else None
    instructor_avg = round(sum(row.instructor_score for row in rows) / count, 2) if enough else None
    themes = list(
        db.scalars(
            select(TrainingFeedbackTheme)
            .where(TrainingFeedbackTheme.course_id == course_id)
            .order_by(TrainingFeedbackTheme.feedback_count.desc(), TrainingFeedbackTheme.id.asc())
        )
    )
    return {
        "course_id": course_id,
        "feedback_count": count,
        "enough_samples": enough,
        "content_avg": content_avg,
        "instructor_avg": instructor_avg,
        "themes": [
            {"theme": row.theme, "count": row.feedback_count}
            for row in themes
            if row.feedback_count >= MIN_FEEDBACK_SAMPLES
        ],
        "improvement_note": course.feedback_note,
        "improvement_note_at": course.feedback_note_at,
    }


def refresh_feedback_themes(
    db: Session,
    course_id: int,
    *,
    admin: User,
    extractor: ThemeExtractor | None = None,
) -> dict[str, Any]:
    """调用真实模型归纳匿名文字意见；只保存达到 5 份支持的主题。"""
    require_admin(admin)
    _require_course(db, course_id)
    rows = list(
        db.scalars(
            select(TrainingFeedback).where(
                TrainingFeedback.course_id == course_id,
                TrainingFeedback.status == "submitted",
            )
        )
    )
    comments = [(row.comment or "").strip() for row in rows if (row.comment or "").strip()]
    db.query(TrainingFeedbackTheme).filter(TrainingFeedbackTheme.course_id == course_id).delete(
        synchronize_session=False
    )
    if not comments:
        db.commit()
        return {"generated": False, "reason": "暂无文字意见可归纳", "comment_count": 0, "theme_count": 0}

    effective = extractor or theme_extractor
    try:
        assignments = effective(comments)
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001 - 模型失败必须明确提示，不能伪造主题
        raise BizError(503, f"主题归纳失败，请稍后重试：{type(exc).__name__}") from exc
    if len(assignments) != len(comments):
        raise BizError(503, "主题归纳结果不完整，请稍后重试")

    counts: dict[str, int] = {}
    for themes in assignments:
        for theme in dict.fromkeys(themes):
            counts[theme] = counts.get(theme, 0) + 1
    qualified = {theme: value for theme, value in counts.items() if value >= MIN_FEEDBACK_SAMPLES}
    now = datetime.now()
    for theme, value in qualified.items():
        db.add(
            TrainingFeedbackTheme(
                course_id=course_id,
                theme=theme,
                feedback_count=value,
                prompt_version=feedback_themes.THEME_PROMPT_VERSION,
                generated_at=now,
            )
        )
    db.commit()
    return {
        "generated": True,
        "reason": None,
        "comment_count": len(comments),
        "theme_count": len(qualified),
    }


def update_feedback_note(
    db: Session, course_id: int, payload: FeedbackNoteRequest, *, admin: User
) -> TrainingCourse:
    """管理员依据汇总发布统一改进说明；不修改课程版本。"""
    require_admin(admin)
    course = _require_course(db, course_id)
    course.feedback_note = payload.note.strip()
    course.feedback_note_at = datetime.now()
    course.updated_by = admin.id
    db.commit()
    db.refresh(course)
    return course


# ---------------------------------------------------------------- 报名审批/候补/名额/超时

ENROLLMENT_TTL_HOURS = 24


def _occupying_count(db: Session, course_id: int, *, exclude_id: int | None = None) -> int:
    """占用名额的报名数：pending 占位、approved 占用；排除指定行用于审批计算。"""
    stmt = select(func.count()).select_from(TrainingEnrollment).where(
        TrainingEnrollment.course_id == course_id,
        TrainingEnrollment.status.in_(("pending", "approved")),
    )
    if exclude_id is not None:
        stmt = stmt.where(TrainingEnrollment.id != exclude_id)
    return int(db.scalar(stmt) or 0)


def _next_session_id(db: Session, course_id: int, *, now: datetime) -> int | None:
    """候补递补通知需要期次引用；取最近未结束期次，没有则跳过通知。"""
    return db.scalar(
        select(TrainingSession.id)
        .where(TrainingSession.course_id == course_id, TrainingSession.ends_at >= now)
        .order_by(TrainingSession.starts_at.asc())
    )


def _reserved_awaiting_count(db: Session, course_id: int) -> int:
    """已通知候补但员工尚未确认的名额预留数：不算正式占位，但限制超发。"""
    return int(
        db.scalar(
            select(func.count())
            .select_from(TrainingEnrollment)
            .where(TrainingEnrollment.course_id == course_id, TrainingEnrollment.status == "awaiting_confirmation")
        )
        or 0
    )


def _promote_waitlist(db: Session, course_id: int, *, now: datetime, actor_user_id: int | None = None) -> TrainingEnrollment | None:
    """名额释放后通知最早候补：进入 `awaiting_confirmation`（非正式占位），24h 内需本人确认。

    需求 F05：有空位通知下一位、员工 24 小时内确认、超时通知下位、确认仍走课程审批规则、不自动报名。
    预留口径：正式占位（pending/approved）+ 待确认预留不得超过容量，避免超发。
    """
    course = db.get(TrainingCourse, course_id)
    if not course or not course.capacity:
        return None
    reserved = _occupying_count(db, course_id) + _reserved_awaiting_count(db, course_id)
    if reserved >= course.capacity:
        return None
    row = db.scalar(
        select(TrainingEnrollment)
        .where(TrainingEnrollment.course_id == course_id, TrainingEnrollment.status == "waitlisted")
        .order_by(TrainingEnrollment.id.asc())
    )
    if not row:
        return None
    row.status = "awaiting_confirmation"
    row.expires_at = now + timedelta(hours=ENROLLMENT_TTL_HOURS)
    row.decided_by = None
    row.decided_at = None
    db.flush()
    session_id = _next_session_id(db, course_id, now=now)
    if session_id is not None:
        try:
            with db.begin_nested():
                notify.course_waitlist_promoted(
                    db,
                    session_id=session_id,
                    recipient_user_id=row.employee_user_id,
                    seq=row.id,
                    deadline=row.expires_at.isoformat(),
                    actor_user_id=actor_user_id,
                )
        except Exception:  # noqa: BLE001 - 通知失败仅回滚 savepoint，不影响递补状态
            pass
    return row


def apply_enrollment(db: Session, payload: EnrollmentRequest, *, user: User) -> TrainingEnrollment:
    """员工自主报名；有名额时 pending 占位 24h，无名额直接进入候补队列。"""
    _require_employee_caller(user)
    course = _require_course(db, payload.course_id)
    if course.status != "active":
        raise BizError(400, "课程未开放")
    existing = db.scalar(
        select(TrainingEnrollment).where(
            TrainingEnrollment.course_id == payload.course_id,
            TrainingEnrollment.employee_user_id == user.id,
        )
    )
    if existing:
        return existing
    full = bool(course.capacity) and _occupying_count(db, payload.course_id) >= int(course.capacity)
    row = TrainingEnrollment(
        course_id=payload.course_id,
        employee_user_id=user.id,
        priority=payload.priority,
        status="waitlisted" if full else "pending",
        expires_at=None if full else datetime.now() + timedelta(hours=ENROLLMENT_TTL_HOURS),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def decide_enrollment(
    db: Session, enrollment_id: int, payload: EnrollmentDecideRequest, *, admin: User
) -> TrainingEnrollment:
    require_admin(admin)
    row = db.get(TrainingEnrollment, enrollment_id)
    if not row:
        raise BizError(404, "报名记录不存在")
    if row.status == "awaiting_confirmation":
        raise BizError(400, "候补名额待员工确认，确认后方可进入审批")
    if row.status not in ("pending", "waitlisted") and not (
        row.status == "approved" and payload.status == "cancelled"
    ):
        raise BizError(400, "该报名已处理")
    now = datetime.now()
    if row.status == "pending" and row.expires_at and row.expires_at < now:
        # 超时占位释放：不再占用名额，员工需重新报名
        row.status = "expired"
        db.commit()
        _promote_waitlist(db, row.course_id, now=now, actor_user_id=admin.id)
        db.commit()
        raise BizError(400, "报名已超时释放，需员工重新申请")
    if payload.status == "approved":
        course = _require_course(db, row.course_id)
        if course.capacity and row.status == "waitlisted":
            if _occupying_count(db, row.course_id, exclude_id=row.id) >= int(course.capacity):
                raise BizError(400, "名额已满，无法通过该候补报名")
    row.status = payload.status
    row.decided_by = admin.id
    row.decided_at = now
    if payload.note is not None:
        row.note = payload.note
    if row.status == "approved":
        row.expires_at = None
    db.commit()
    db.refresh(row)
    notified = True
    try:
        # 通知 D 域：报名审批结果（rejected/cancelled 也告知，幂等键含结果）
        notify.course_enrollment_reviewed(
            db,
            enrollment_id=row.id,
            recipient_user_id=row.employee_user_id,
            result=row.status,
            actor_user_id=admin.id,
        )
    except Exception:  # noqa: BLE001 - 通知失败不回滚已生效的业务状态
        notified = False
        db.rollback()
    if row.status in ("rejected", "cancelled"):
        # 释放名额后递补最早候补
        if not notified:
            db.refresh(row)
        _promote_waitlist(db, row.course_id, now=now, actor_user_id=admin.id)
    db.commit()
    db.refresh(row)
    return row


def confirm_waitlist_offer(
    db: Session, enrollment_id: int, *, user: User
) -> TrainingEnrollment:
    """员工确认候补名额（24h 内）：`awaiting_confirmation` → `pending`，随后仍走课程审批规则。

    确认不等于自动报名成功，仅把预留转为待审批占位；超期或状态不符均拒绝。
    """
    _require_employee_caller(user)
    row = db.get(TrainingEnrollment, enrollment_id)
    if not row:
        raise BizError(404, "报名记录不存在")
    if row.employee_user_id != user.id:
        raise BizError(403, "只能确认本人的候补名额")
    if row.status != "awaiting_confirmation":
        raise BizError(400, "当前状态不可确认候补名额")
    now = datetime.now()
    if row.expires_at and row.expires_at < now:
        row.status = "expired"
        db.commit()
        _promote_waitlist(db, row.course_id, now=now, actor_user_id=user.id)
        db.commit()
        raise BizError(400, "候补名额已超时释放，请重新报名或等待下一次通知")
    course = _require_course(db, row.course_id)
    if course.capacity and _occupying_count(db, row.course_id) >= int(course.capacity):
        raise BizError(400, "名额已满，暂无法确认，请联系培训管理员")
    row.status = "pending"
    row.expires_at = now + timedelta(hours=ENROLLMENT_TTL_HOURS)
    db.commit()
    db.refresh(row)
    return row


def release_expired_enrollments(db: Session, *, now: datetime | None = None, actor: User | None = None) -> dict[str, int]:
    """扫描超时未决定的占位并释放，随后对每个受影响课程递补候补。

    覆盖两类超时：`pending` 审批占位超时，以及 `awaiting_confirmation` 候补确认超时（超时通知下位）。
    """
    if actor is not None:
        require_admin(actor)
    moment = now or datetime.now()
    rows = list(
        db.scalars(
            select(TrainingEnrollment).where(
                TrainingEnrollment.status.in_(("pending", "awaiting_confirmation")),
                TrainingEnrollment.expires_at.is_not(None),
                TrainingEnrollment.expires_at < moment,
            )
        )
    )
    course_ids = {row.course_id for row in rows}
    for row in rows:
        row.status = "expired"
    db.flush()
    promoted = 0
    for course_id in course_ids:
        if _promote_waitlist(db, course_id, now=moment, actor_user_id=actor.id if actor else None) is not None:
            promoted += 1
    db.commit()
    return {"scanned": len(rows), "released": len(rows), "promoted": promoted}


def list_enrollments(db: Session, *, course_id: int | None = None, user: User) -> list[TrainingEnrollment]:
    stmt = select(TrainingEnrollment).order_by(TrainingEnrollment.id.desc())
    if is_admin(user):
        if course_id is not None:
            stmt = stmt.where(TrainingEnrollment.course_id == course_id)
    else:
        stmt = stmt.where(TrainingEnrollment.employee_user_id == user.id)
    return list(db.scalars(stmt))


# ---------------------------------------------------------------- 防挂机/有效学习证据

# 一期固定阈值：课程暂未落库“完成条件配置”，先用统一常量；课程可配后替换为按课程取值。
FORMAL_COMPLETION_MIN_SCORE = 60.0
COURSE_STUDY_TARGET_MINUTES = 45.0  # 单门课有效学时目标，累计达到即计满分
MIN_STUDY_SESSION_MINUTES = 5.0  # 单次学习会话最短有效时长，避免“打开即完成”
STUDY_SESSION_TTL_MINUTES = 120  # 学习会话凭证有效期

EVIDENCE_PROVIDER_ATTENDANCE = "system.attendance"
EVIDENCE_PROVIDER_ASSESSMENT = "system.assessment"
EVIDENCE_PROVIDER_STUDY = "system.study_session"
TRUSTED_EVIDENCE_PROVIDERS = frozenset(
    {EVIDENCE_PROVIDER_ATTENDANCE, EVIDENCE_PROVIDER_ASSESSMENT, EVIDENCE_PROVIDER_STUDY}
)


def _evidence_record(db: Session, learning_record_id: int, user: User) -> TrainingLearningRecord:
    """证据只属于员工本人，且计划已分配；未分配计划不产生可信学习数据。"""
    _require_employee_caller(user)
    record = db.get(TrainingLearningRecord, learning_record_id)
    if record is None or record.employee_user_id != user.id:
        raise BizError(403, "只能提交本人学习记录的证据")
    plan_item = db.get(TrainingPlanItem, record.plan_item_id)
    if plan_item is None:
        raise BizError(400, "学习记录缺少培训计划项，不能认定学习证据")
    plan = db.get(TrainingPlan, plan_item.plan_id)
    if plan is None or plan.employee_user_id != user.id:
        raise BizError(403, "只能提交本人学习记录的证据")
    if plan.status != "assigned":
        raise BizError(400, "计划尚未分配，不能产生学习证据")
    return record


def _study_token_secret() -> bytes:
    """会话凭证由服务端签名，客户端无法伪造签发时间或替他人领取。"""
    return settings.jwt_secret.encode("utf-8")


def _sign_study_token(record_id: int, employee_id: int, issued_epoch: int, nonce: str) -> str:
    body = f"{record_id}:{employee_id}:{issued_epoch}:{nonce}"
    digest = hmac.new(_study_token_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}:{digest}"


def issue_study_session(
    db: Session, learning_record_id: int, *, user: User, now: datetime | None = None
) -> dict[str, Any]:
    """员工领取一次性学习会话凭证；有效时长由服务端按签发与兑换时间差计算。"""
    record = _evidence_record(db, learning_record_id, user)
    moment = now or datetime.now()
    nonce = secrets.token_hex(8)
    return {
        "learning_record_id": record.id,
        "session_token": _sign_study_token(record.id, user.id, int(moment.timestamp()), nonce),
        "expires_at": (moment + timedelta(minutes=STUDY_SESSION_TTL_MINUTES)).isoformat(),
    }


def _parse_study_token(token: str, *, record_id: int, employee_id: int, now: datetime) -> tuple[str, float]:
    """校验签名/归属/有效期，返回 (nonce, 服务端计算的有效学习分钟数)。"""
    parts = token.split(":")
    if len(parts) != 5 or not all(part for part in parts):
        raise BizError(400, "学习会话凭证格式无效")
    raw_record, raw_employee, raw_epoch, nonce, digest = parts
    expected = hmac.new(
        _study_token_secret(), ":".join(parts[:4]).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, digest):
        raise BizError(400, "学习会话凭证签名无效")
    if not (raw_record.isdigit() and raw_employee.isdigit() and raw_epoch.isdigit()):
        raise BizError(400, "学习会话凭证格式无效")
    if int(raw_record) != record_id or int(raw_employee) != employee_id:
        raise BizError(403, "学习会话凭证不属于本人学习记录")
    issued_at = datetime.fromtimestamp(int(raw_epoch))
    elapsed_minutes = (now - issued_at).total_seconds() / 60.0
    if elapsed_minutes < 0:
        raise BizError(400, "学习会话凭证签发时间异常")
    if elapsed_minutes > STUDY_SESSION_TTL_MINUTES:
        raise BizError(400, "学习会话凭证已过期，请重新开始学习")
    if elapsed_minutes < MIN_STUDY_SESSION_MINUTES:
        raise BizError(
            400, f"有效学习时长不足 {MIN_STUDY_SESSION_MINUTES:g} 分钟，本次不计入证据"
        )
    return nonce, elapsed_minutes


def _attendance_evidence(db: Session, record: TrainingLearningRecord) -> tuple[float, str, dict, str]:
    plan_item = db.get(TrainingPlanItem, record.plan_item_id)
    attendance = db.scalars(
        select(TrainingAttendance)
        .join(TrainingSession, TrainingSession.id == TrainingAttendance.session_id)
        .where(
            TrainingSession.course_id == plan_item.course_id,
            TrainingAttendance.employee_user_id == record.employee_user_id,
        )
        .order_by(TrainingAttendance.id.desc())
    ).first()
    if attendance is None:
        raise BizError(400, "未查到本人该课程的真实出勤记录，不能作为学习证据")
    detail = {
        "attendance_id": attendance.id,
        "session_id": attendance.session_id,
        "course_id": plan_item.course_id,
    }
    return 100.0, EVIDENCE_PROVIDER_ATTENDANCE, detail, f"attendance:{attendance.id}"


def _assessment_evidence(db: Session, record: TrainingLearningRecord) -> tuple[float, str, dict, str]:
    result = db.scalars(
        select(AssessmentResult)
        .join(AssessmentAssignment, AssessmentAssignment.id == AssessmentResult.assignment_id)
        .where(
            AssessmentAssignment.user_id == record.employee_user_id,
            AssessmentAssignment.purpose != "recruitment",
            AssessmentResult.passed == 1,
        )
        .order_by(AssessmentResult.id.desc())
    ).first()
    if result is None:
        raise BizError(400, "未查到本人已通过的员工测评结果，不能作为学习证据")
    detail = {
        "result_id": result.id,
        "version": result.version,
        "total_score": float(result.total_score),
    }
    return 100.0, EVIDENCE_PROVIDER_ASSESSMENT, detail, f"assessment:{result.id}:v{result.version}"


def _study_evidence(
    db: Session,
    record: TrainingLearningRecord,
    payload: dict,
    *,
    employee_id: int,
    now: datetime,
) -> tuple[float, str, dict, str]:
    token = str(payload.get("session_token") or "").strip()
    if not token:
        raise BizError(400, "学习会话凭证缺失，请先领取 study-sessions 凭证")
    nonce, minutes = _parse_study_token(
        token, record_id=record.id, employee_id=employee_id, now=now
    )
    previous = list(
        db.scalars(
            select(TrainingLearningEvidence).where(
                TrainingLearningEvidence.learning_record_id == record.id,
                TrainingLearningEvidence.evidence_type == "study_session",
                TrainingLearningEvidence.verified_by == EVIDENCE_PROVIDER_STUDY,
            )
        )
    )
    prior_minutes = sum(float((row.payload_json or {}).get("minutes") or 0) for row in previous)
    cumulative = prior_minutes + minutes
    score = min(100.0, round(cumulative / COURSE_STUDY_TARGET_MINUTES * 100, 2))
    detail = {"minutes": round(minutes, 2), "cumulative_minutes": round(cumulative, 2)}
    return score, EVIDENCE_PROVIDER_STUDY, detail, f"study_session:{nonce}"


def submit_evidence(
    db: Session, payload: EvidenceSubmitRequest, *, user: User, now: datetime | None = None
) -> TrainingLearningEvidence:
    """员工提交学习证据；分数与验证人由服务端可信来源计算，客户端不能自报。

    同一可信来源（出勤/测评结果/会话 nonce）在一条学习记录内只能计入一次，
    用于阻止重复计数与凭证重放。
    """
    record = _evidence_record(db, payload.learning_record_id, user)
    moment = now or datetime.now()
    raw_payload = dict(payload.payload or {})
    if payload.evidence_type == "attendance":
        score, provider, detail, evidence_key = _attendance_evidence(db, record)
    elif payload.evidence_type == "assessment":
        score, provider, detail, evidence_key = _assessment_evidence(db, record)
    else:
        score, provider, detail, evidence_key = _study_evidence(
            db, record, raw_payload, employee_id=user.id, now=moment
        )
    duplicate = db.scalar(
        select(TrainingLearningEvidence).where(
            TrainingLearningEvidence.learning_record_id == record.id,
            TrainingLearningEvidence.evidence_key == evidence_key,
        )
    )
    if duplicate is not None:
        raise BizError(400, "该学习证据已提交，同一来源不能重复计入")
    evidence = TrainingLearningEvidence(
        learning_record_id=record.id,
        employee_user_id=user.id,
        evidence_type=payload.evidence_type,
        evidence_key=evidence_key,
        payload_json=detail,
        score=score,
        verified_by=provider,
    )
    db.add(evidence)
    try:
        db.flush()
    except IntegrityError as exc:  # 并发重复提交时由唯一约束兜底
        db.rollback()
        raise BizError(400, "该学习证据已提交，同一来源不能重复计入") from exc
    rows = list(
        db.scalars(
            select(TrainingLearningEvidence).where(
                TrainingLearningEvidence.learning_record_id == record.id
            )
        )
    )
    record.evidence_score = max(float(row.score) for row in rows)
    db.commit()
    db.refresh(evidence)
    return evidence


def confirm_formal_completion(db: Session, learning_record_id: int, *, admin: User) -> TrainingLearningRecord:
    """管理员依据可信证据认定正式完成；仅系统校验证据计数，且须达到完成阈值。"""
    require_admin(admin)
    record = db.get(TrainingLearningRecord, learning_record_id)
    if not record:
        raise BizError(404, "学习记录不存在")
    rows = list(
        db.scalars(
            select(TrainingLearningEvidence).where(
                TrainingLearningEvidence.learning_record_id == learning_record_id,
                TrainingLearningEvidence.verified_by.in_(TRUSTED_EVIDENCE_PROVIDERS),
            )
        )
    )
    if not rows:
        raise BizError(400, "无有效学习证据，不能认定正式完成")
    best = max(float(row.score) for row in rows)
    if best < FORMAL_COMPLETION_MIN_SCORE:
        raise BizError(
            400,
            f"有效学习证据得分 {best:g} 未达到完成阈值 {FORMAL_COMPLETION_MIN_SCORE:g}，"
            "不能认定正式完成",
        )
    was_formal = bool(record.formal_completed)
    record.formal_completed = 1
    record.status = "completed"
    record.evidence_score = best
    if not was_formal:
        # 正式完成（区别于员工自报 course_completed）追加成长事件，引用真实计划版本与可信证据
        item = db.get(TrainingPlanItem, record.plan_item_id)
        plan = db.get(TrainingPlan, item.plan_id) if item is not None else None
        title = (item.course_snapshot_json or {}).get("title") if item is not None else None
        db.add(
            TrainingGrowthEvent(
                employee_user_id=record.employee_user_id,
                talent_profile_id=plan.talent_profile_id if plan is not None else None,
                source_type="training",
                event_type="course_formally_completed",
                ref_type="training_plan_item",
                ref_id=record.plan_item_id,
                summary=f"正式完成课程：{title or (item.course_id if item is not None else record.plan_item_id)}",
                payload_json={
                    "plan_id": plan.id if plan is not None else None,
                    "version": plan.version if plan is not None else None,
                    "learning_record_id": record.id,
                    "self_reported": False,
                    "evidence_score": best,
                    "evidence_providers": sorted({row.verified_by for row in rows if row.verified_by}),
                },
            )
        )
    db.commit()
    db.refresh(record)
    return record


def list_evidence(db: Session, learning_record_id: int, *, user: User) -> list[TrainingLearningEvidence]:
    record = db.get(TrainingLearningRecord, learning_record_id)
    if not record:
        raise BizError(404, "学习记录不存在")
    if not is_admin(user) and record.employee_user_id != user.id:
        raise BizError(403, "无权查看他人证据")
    return list(
        db.scalars(
            select(TrainingLearningEvidence)
            .where(TrainingLearningEvidence.learning_record_id == learning_record_id)
            .order_by(TrainingLearningEvidence.id.asc())
        )
    )


# ---------------------------------------------------------------- 必修到期/逾期/延期（F09）

def _ensure_learning_record(db: Session, item: TrainingPlanItem, employee_user_id: int) -> TrainingLearningRecord:
    """到期扫描需要学习记录做通知引用；缺失时建系统占位记录（非员工自报）。"""
    record = db.scalar(
        select(TrainingLearningRecord).where(
            TrainingLearningRecord.plan_item_id == item.id,
            TrainingLearningRecord.employee_user_id == employee_user_id,
        )
    )
    if record is None:
        record = TrainingLearningRecord(
            plan_item_id=item.id, employee_user_id=employee_user_id,
            status="not_started", is_self_reported=0,
        )
        db.add(record)
        db.flush()
    return record


def decide_extension(
    db: Session, item_id: int, payload: ExtensionDecideRequest, *, admin: User
) -> TrainingPlanItem:
    """管理员延长期限：必须填写原因，保留旧期限到事件，通知员工（及可选经理）。"""
    require_admin(admin)
    item = db.get(TrainingPlanItem, item_id)
    if item is None:
        raise BizError(404, "培训计划项不存在")
    plan = _require_plan(db, item.plan_id)
    if plan.status != "assigned":
        raise BizError(400, "只有已分配计划可延长期限")
    old_due = item.due_date
    item.due_date = payload.new_due_date
    record = _ensure_learning_record(db, item, plan.employee_user_id)
    recipients = [plan.employee_user_id]
    if payload.manager_user_id:
        recipients.append(payload.manager_user_id)
    db.add(
        TrainingGrowthEvent(
            employee_user_id=plan.employee_user_id,
            talent_profile_id=plan.talent_profile_id,
            event_type="training_due_extended",
            ref_type="training_plan_item",
            ref_id=item.id,
            summary=f"必修期限 {old_due or '未设置'} → {payload.new_due_date}",
            payload_json={
                "old_due_date": old_due.isoformat() if old_due else None,
                "new_due_date": payload.new_due_date.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    db.commit()
    db.refresh(item)
    notify.training_extension_decided(
        db,
        record_id=record.id,
        recipients=recipients,
        new_due_date=payload.new_due_date.isoformat(),
        reason=payload.reason,
        actor_user_id=admin.id,
    )
    db.commit()
    return item


def _assigned_items_with_due(db: Session) -> list[tuple[TrainingPlanItem, int, int | None]]:
    rows = db.execute(
        select(TrainingPlanItem, TrainingPlan.employee_user_id, TrainingPlan.talent_profile_id)
        .join(TrainingPlan, TrainingPlan.id == TrainingPlanItem.plan_id)
        .where(TrainingPlan.status == "assigned", TrainingPlanItem.due_date.is_not(None))
    ).all()
    return [(item, employee_id, talent_id) for item, employee_id, talent_id in rows]


def emit_due_reminders(
    db: Session, *, today: date, days_ahead: int = 3, manager_user_id: int | None = None, admin: User
) -> dict[str, int]:
    """扫描 [today, today+days_ahead] 到期且未完成的学习记录并投递提醒（幂等）。"""
    require_admin(admin)
    limit = today + timedelta(days=days_ahead)
    scanned = emitted = 0
    for item, employee_id, _ in _assigned_items_with_due(db):
        if not (today <= item.due_date <= limit):
            continue
        scanned += 1
        record = _ensure_learning_record(db, item, employee_id)
        if record.status == "completed":
            continue
        recipients = [employee_id] + ([manager_user_id] if manager_user_id else [])
        notify.training_due_reminder(
            db, record_id=record.id, recipients=recipients, due_date=item.due_date.isoformat()
        )
        emitted += 1
    db.commit()
    return {"scanned": scanned, "emitted": emitted}


def emit_overdue(
    db: Session, *, today: date, manager_user_id: int | None = None, admin: User
) -> dict[str, int]:
    """扫描已过期且未完成的学习记录并投递逾期通知（同一记录仅一次，幂等键无日期）。"""
    require_admin(admin)
    scanned = emitted = 0
    for item, employee_id, _ in _assigned_items_with_due(db):
        if item.due_date >= today:
            continue
        scanned += 1
        record = _ensure_learning_record(db, item, employee_id)
        if record.status == "completed":
            continue
        recipients = [employee_id] + ([manager_user_id] if manager_user_id else [])
        notify.training_overdue(db, record_id=record.id, recipients=recipients)
        emitted += 1
    db.commit()
    return {"scanned": scanned, "emitted": emitted}
