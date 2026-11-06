"""C 培训成长域服务（契约 §5）。

- 仅员工可被分配培训；候选人/管理员本人无培训任务（后端校验，不只隐藏按钮）。
- 课程只接受真实 http/https 材料链接；草稿只从有效课程目录选择，不编造链接。
- 计划保留版本快照；员工学习状态为自报，完成不等于验证学时或能力评级变化。
- 员工测评可生成培训草稿；招聘测评明确跳过（C-Q13/固定流程口径）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.models import User
from app.modules.assessments.models import AssessmentAssignment, AssessmentResult
from app.modules.assessments.service import is_admin, require_admin
from app.modules.training import drafting
from app.modules.training.models import (
    TrainingCourse,
    TrainingGrowthEvent,
    TrainingLearningRecord,
    TrainingPlan,
    TrainingPlanItem,
    TrainingPlanVersion,
)
from app.modules.training.schemas import (
    CourseCreateRequest,
    CourseUpdateRequest,
    DraftRequest,
    PlanCreateRequest,
    PlanItemIn,
    PlanUpdateRequest,
)

DraftGenerator = Callable[..., drafting.DraftResult]


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


def plan_versions(db: Session, plan: TrainingPlan) -> list[TrainingPlanVersion]:
    return list(
        db.scalars(
            select(TrainingPlanVersion)
            .where(TrainingPlanVersion.plan_id == plan.id)
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
