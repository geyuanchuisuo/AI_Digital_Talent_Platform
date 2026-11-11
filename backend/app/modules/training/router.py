"""C 培训成长域路由（prefix `/api/training`）。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination
from app.core.response import ok, paginated
from app.database import get_db
from app.modules.training import service
from app.modules.training.models import (
    CapabilityRating,
    CapabilityRatingChange,
    PrejobPhase,
    PrejobPlan,
    TrainingAttendance,
    TrainingCourse,
    TrainingGrowthEvent,
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
    PlanUpdateRequest,
    PrejobPlanCreateRequest,
    PrejobPlanUpdateRequest,
    ProgressUpdateRequest,
    ReminderScanRequest,
    SessionCreateRequest,
)

router = APIRouter(prefix="/api/training", tags=["training"])


def _course_out(row: TrainingCourse) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "material_url": row.material_url,
        "dimension_names": list(row.dimension_names_json or []),
        "capacity": row.capacity,
        "status": row.status,
        "version": row.version,
        "feedback_note": row.feedback_note,
        "feedback_note_at": row.feedback_note_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _item_out(
    item: TrainingPlanItem,
    record: TrainingLearningRecord | None = None,
    feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "id": item.id,
        "plan_id": item.plan_id,
        "plan_version": item.plan_version,
        "course_id": item.course_id,
        "course_snapshot": dict(item.course_snapshot_json or {}),
        "dimension_name": item.dimension_name,
        "reason": item.reason,
        "sort_order": item.sort_order,
        "learning": {
            "record_id": record.id if record else None,
            "status": record.status if record else "not_started",
            "note": record.note if record else None,
            "reported_at": record.reported_at if record else None,
            "is_self_reported": bool(record.is_self_reported) if record else True,
            "formal_completed": bool(record.formal_completed) if record else False,
        },
        "feedback": feedback,
    }
    return data


def _plan_out(
    plan: TrainingPlan,
    items: list[TrainingPlanItem] | None = None,
    *,
    records: dict[int, TrainingLearningRecord] | None = None,
    feedback_by_course: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": plan.id,
        "employee_user_id": plan.employee_user_id,
        "talent_profile_id": plan.talent_profile_id,
        "job_id": plan.job_id,
        "target_job_title": plan.target_job_title,
        "source": plan.source,
        "status": plan.status,
        "gap_summary": plan.gap_summary,
        "version": plan.version,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }
    if items is not None:
        records = records or {}
        feedback_by_course = feedback_by_course or {}
        data["items"] = [
            _item_out(item, records.get(item.id), feedback_by_course.get(item.course_id))
            for item in items
        ]
    return data


def _version_out(row: TrainingPlanVersion) -> dict[str, Any]:
    return {
        "version": row.version,
        "status": row.status,
        "snapshot": dict(row.snapshot_json or {}),
        "note": row.note,
        "created_at": row.created_at,
    }


def _growth_out(row: TrainingGrowthEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "employee_user_id": row.employee_user_id,
        "talent_profile_id": row.talent_profile_id,
        "event_type": row.event_type,
        "ref_type": row.ref_type,
        "ref_id": row.ref_id,
        "summary": row.summary,
        "payload": dict(row.payload_json or {}),
        "created_at": row.created_at,
    }


def _session_out(row: TrainingSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "course_id": row.course_id,
        "instructor_name": row.instructor_name,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "created_at": row.created_at,
    }


def _attendance_out(row: TrainingAttendance) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "employee_user_id": row.employee_user_id,
        "attended_at": row.attended_at,
    }


# ---------------------------------------------------------------- 课程

@router.post("/courses", summary="新建课程（管理员，真实材料链接）")
def create_course(
    payload: CourseCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.create_course(db, payload, admin=user)
    return ok(_course_out(row))


@router.get("/courses", summary="课程目录")
def list_courses(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    status: str | None = Query(None),
    keyword: str | None = Query(None, max_length=100),
):
    rows, total = service.list_courses(
        db, status=status, keyword=keyword, offset=page.offset, limit=page.limit
    )
    return paginated([_course_out(row) for row in rows], total, page.page, page.page_size)


@router.patch("/courses/{course_id}", summary="修改课程（管理员）")
def update_course(
    course_id: int,
    payload: CourseUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.update_course(db, course_id, payload, admin=user)
    return ok(_course_out(row))


# ---------------------------------------------------------------- 期次、出勤与评价

@router.post("/courses/{course_id}/sessions", summary="新建课程期次（管理员）")
def create_session(
    course_id: int,
    payload: SessionCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.create_session(db, course_id, payload, admin=user)
    return ok(_session_out(row))


@router.get("/courses/{course_id}/sessions", summary="课程期次列表（管理员）")
def list_sessions(
    course_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.list_sessions(db, course_id, admin=user)
    return ok({"items": [_session_out(row) for row in rows], "total": len(rows)})


@router.post("/sessions/{session_id}/attendance", summary="登记实际出勤（管理员）")
def mark_attendance(
    session_id: int,
    payload: AttendanceMarkRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.mark_attendance(db, session_id, payload, admin=user)
    return ok(_attendance_out(row))


@router.get("/sessions/{session_id}/attendance", summary="期次出勤名单（管理员）")
def list_attendance(
    session_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.list_attendance(db, session_id, admin=user)
    return ok({"items": [_attendance_out(row) for row in rows], "total": len(rows)})


@router.get("/courses/{course_id}/feedback/mine", summary="本人培训评价状态（员工）")
def my_feedback(
    course_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(service.my_feedback(db, course_id, user=user))


@router.post("/courses/{course_id}/feedback", summary="提交课程评价（员工，窗口内）")
def submit_feedback(
    course_id: int,
    payload: FeedbackSubmitRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.submit_feedback(db, course_id, payload, user=user)
    return ok(service.my_feedback(db, course_id, user=user))


@router.patch("/courses/{course_id}/feedback", summary="修改课程评价（员工，窗口内）")
def update_feedback(
    course_id: int,
    payload: FeedbackSubmitRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.submit_feedback(db, course_id, payload, user=user)
    return ok(service.my_feedback(db, course_id, user=user))


@router.post("/courses/{course_id}/feedback/withdraw", summary="撤回课程评价（员工，窗口内）")
def withdraw_feedback(
    course_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.withdraw_feedback(db, course_id, user=user)
    return ok(service.my_feedback(db, course_id, user=user))


@router.get("/courses/{course_id}/feedback/summary", summary="匿名反馈汇总（管理员，样本门槛）")
def feedback_summary(
    course_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(service.feedback_summary(db, course_id, admin=user))


@router.post(
    "/courses/{course_id}/feedback-themes/refresh",
    summary="匿名文字意见主题归纳（管理员，真实模型）",
)
def refresh_feedback_themes(
    course_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(service.refresh_feedback_themes(db, course_id, admin=user))


@router.put("/courses/{course_id}/feedback-note", summary="发布统一改进说明（管理员）")
def update_feedback_note(
    course_id: int,
    payload: FeedbackNoteRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.update_feedback_note(db, course_id, payload, admin=user)
    return ok(_course_out(row))


# ---------------------------------------------------------------- 报名审批/候补


@router.post("/enrollments", summary="员工报名课程")
def post_enrollment(
    payload: EnrollmentRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.apply_enrollment(db, payload, user=user)
    return ok({"id": row.id, "status": row.status, "expires_at": row.expires_at.isoformat() if row.expires_at else None})


@router.post("/enrollments/{enrollment_id}/confirm", summary="员工确认候补名额（24h 内）")
def post_enrollment_confirm(
    enrollment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.confirm_waitlist_offer(db, enrollment_id, user=user)
    return ok({"id": row.id, "status": row.status, "expires_at": row.expires_at.isoformat() if row.expires_at else None})


@router.post("/enrollments/{enrollment_id}/decide", summary="审批报名")
def post_enrollment_decide(
    enrollment_id: int,
    payload: EnrollmentDecideRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.decide_enrollment(db, enrollment_id, payload, admin=user)
    return ok({"id": row.id, "status": row.status})


@router.get("/enrollments", summary="报名列表")
def get_enrollments(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    course_id: int | None = None,
):
    rows = service.list_enrollments(db, course_id=course_id, user=user)
    return ok([
        {"id": r.id, "course_id": r.course_id, "employee_user_id": r.employee_user_id,
         "priority": r.priority, "status": r.status,
         "expires_at": r.expires_at.isoformat() if r.expires_at else None, "note": r.note}
        for r in rows
    ])


@router.post("/enrollments/release-expired", summary="释放超时未决定的报名占位并递补候补（管理员）")
def post_enrollment_release_expired(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(service.release_expired_enrollments(db, actor=user))


# ---------------------------------------------------------------- 防挂机/有效学习证据


@router.post("/learning-records/{record_id}/study-sessions", summary="领取学习会话凭证（员工本人）")
def post_study_session(
    record_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(service.issue_study_session(db, record_id, user=user))


@router.post("/learning-evidence", summary="提交学习证据（分数/验证人由服务端计算）")
def post_evidence(
    payload: EvidenceSubmitRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.submit_evidence(db, payload, user=user)
    return ok({
        "id": row.id,
        "evidence_type": row.evidence_type,
        "score": row.score,
        "verified_by": row.verified_by,
    })


@router.post("/learning-records/{record_id}/confirm-completion", summary="管理员认定正式完成")
def post_confirm_completion(
    record_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.confirm_formal_completion(db, record_id, admin=user)
    return ok({"id": row.id, "formal_completed": bool(row.formal_completed)})


@router.get("/learning-records/{record_id}/evidence", summary="学习证据列表")
def get_evidence(
    record_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.list_evidence(db, record_id, user=user)
    return ok([
        {"id": r.id, "evidence_type": r.evidence_type, "score": r.score,
         "verified_by": r.verified_by, "payload": r.payload_json}
        for r in rows
    ])


# ---------------------------------------------------------------- 必修到期/逾期/延期（F09）


@router.post("/plan-items/{item_id}/extension", summary="管理员延期必修期限（须填原因）")
def post_extension(
    item_id: int,
    payload: ExtensionDecideRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    item = service.decide_extension(db, item_id, payload, admin=user)
    return ok({
        "id": item.id,
        "due_date": item.due_date.isoformat() if item.due_date else None,
    })


@router.post("/reminders/scan", summary="扫描必修到期/逾期并投递通知（管理员）")
def post_reminder_scan(
    payload: ReminderScanRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    today = payload.today or datetime.now().date()
    due = service.emit_due_reminders(db, today=today, days_ahead=payload.days_ahead, admin=user)
    overdue = service.emit_overdue(db, today=today, admin=user)
    return ok({"today": today.isoformat(), "due": due, "overdue": overdue})


# ---------------------------------------------------------------- 草稿（静态路径需先注册）

@router.post("/plans/draft", summary="生成 AI 培训草稿（真实课程目录）")
def create_draft(
    payload: DraftRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    data = service.create_ai_draft(db, payload, admin=user)
    plan = data["plan"]
    items = service.plan_items(db, plan) if plan is not None else []
    return ok(
        {
            "generated": data["generated"],
            "reason": data["reason"],
            "gap_message": data["gap_message"],
            "plan": _plan_out(plan, items) if plan is not None else None,
        }
    )


@router.post(
    "/plans/draft-from-assessment/{assignment_id}",
    summary="员工测评固定流程 → 培训草稿（招聘跳过）",
)
def draft_from_assessment(
    assignment_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    data = service.draft_from_assessment(db, assignment_id, admin=user)
    plan = data["plan"]
    items = service.plan_items(db, plan) if plan is not None else []
    return ok(
        {
            "generated": data["generated"],
            "reason": data["reason"],
            "gap_message": data["gap_message"],
            "plan": _plan_out(plan, items) if plan is not None else None,
        }
    )


# ---------------------------------------------------------------- 计划

@router.post("/plans", summary="手工创建培训计划草稿（管理员）")
def create_plan(
    payload: PlanCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.create_manual_plan(db, payload, admin=user)
    return ok(_plan_out(plan, service.plan_items(db, plan)))


@router.get("/plans", summary="培训计划列表（管理员）")
def list_plans(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    employee_user_id: int | None = Query(None),
    status: str | None = Query(None),
):
    service.require_admin(user)
    rows, total = service.list_plans(
        db,
        employee_user_id=employee_user_id,
        status=status,
        offset=page.offset,
        limit=page.limit,
    )
    return paginated([_plan_out(row) for row in rows], total, page.page, page.page_size)


@router.get("/my/plans", summary="我的培训计划（仅员工）")
def my_plans(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.my_plans(db, user)
    items_payload = []
    for item in rows:
        feedback_by_course: dict[int, dict[str, Any]] = {}
        for plan_item in item["items"]:
            if plan_item.course_id in feedback_by_course:
                continue
            course = db.get(TrainingCourse, plan_item.course_id)
            if course is not None:
                feedback_by_course[course.id] = service.feedback_state(db, course, user)
        items_payload.append(
            _plan_out(
                item["plan"],
                item["items"],
                records=service.learning_records(db, item["items"], user.id),
                feedback_by_course=feedback_by_course,
            )
        )
    return ok({"items": items_payload, "total": len(rows)})


@router.get("/plans/{plan_id}", summary="培训计划详情（管理员）")
def get_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_admin(user)
    plan = service.get_plan(db, plan_id)
    items = service.plan_items(db, plan)
    data = _plan_out(
        plan, items, records=service.learning_records(db, items, plan.employee_user_id)
    )
    data["versions"] = [_version_out(row) for row in service.plan_versions(db, plan)]
    return ok(data)


@router.patch("/plans/{plan_id}", summary="修改草稿计划（管理员）")
def update_plan(
    plan_id: int,
    payload: PlanUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.update_plan(db, plan_id, payload, admin=user)
    return ok(_plan_out(plan, service.plan_items(db, plan)))


@router.post("/plans/{plan_id}/assign", summary="确认分配计划（管理员）")
def assign_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.assign_plan(db, plan_id, admin=user)
    return ok(_plan_out(plan, service.plan_items(db, plan)))


@router.post("/plans/{plan_id}/revise", summary="已分配计划生成新版本草稿")
def revise_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.revise_plan(db, plan_id, admin=user)
    return ok(_plan_out(plan, service.plan_items(db, plan)))


# ---------------------------------------------------------------- 岗前培养计划（F05，待上岗人员）


def _prejob_out(plan: PrejobPlan, phases: list[PrejobPhase]) -> dict[str, Any]:
    return {
        "id": plan.id,
        "employee_user_id": plan.employee_user_id,
        "talent_profile_id": plan.talent_profile_id,
        "target_job_title": plan.target_job_title,
        "goal": plan.goal,
        "status": plan.status,
        "version": plan.version,
        "phases": [
            {
                "seq": ph.seq,
                "name": ph.name,
                "goal": ph.goal,
                "mentor_user_id": ph.mentor_user_id,
                "status": ph.status,
                "courses": list(ph.courses_json or []),
            }
            for ph in phases
        ],
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


@router.post("/prejob-plans", summary="创建岗前培养计划草稿（管理员）")
def create_prejob_plan(
    payload: PrejobPlanCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.create_prejob_plan(db, payload, admin=user)
    return ok(_prejob_out(plan, service.get_prejob_plan(db, plan.id, user=user)[1]))


@router.get("/prejob-plans", summary="岗前培养计划列表（管理员全量 / 员工本人）")
def list_prejob_plans(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    rows = service.list_prejob_plans(db, user=user)
    return ok([_prejob_out(plan, service._prejob_phase_rows(db, plan)) for plan in rows])


@router.get("/prejob-plans/{plan_id}", summary="岗前培养计划详情")
def get_prejob_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan, phases = service.get_prejob_plan(db, plan_id, user=user)
    return ok(_prejob_out(plan, phases))


@router.patch("/prejob-plans/{plan_id}", summary="修改岗前计划草稿（管理员）")
def update_prejob_plan(
    plan_id: int,
    payload: PrejobPlanUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.update_prejob_plan(db, plan_id, payload, admin=user)
    return ok(_prejob_out(plan, service.get_prejob_plan(db, plan_id, user=user)[1]))


@router.post("/prejob-plans/{plan_id}/assign", summary="确认分配岗前计划（管理员）")
def assign_prejob_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.assign_prejob_plan(db, plan_id, admin=user)
    return ok(_prejob_out(plan, service.get_prejob_plan(db, plan_id, user=user)[1]))


@router.post("/prejob-plans/{plan_id}/revise", summary="已分配岗前计划生成新版本草稿")
def revise_prejob_plan(
    plan_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    plan = service.revise_prejob_plan(db, plan_id, admin=user)
    return ok(_prejob_out(plan, service.get_prejob_plan(db, plan_id, user=user)[1]))


# ---------------------------------------------------------------- 正式能力评级变更链（C14）

def _capability_change_out(row: CapabilityRatingChange) -> dict[str, Any]:
    return {
        "id": row.id,
        "employee_user_id": row.employee_user_id,
        "talent_profile_id": row.talent_profile_id,
        "dimension_name": row.dimension_name,
        "evidence": list(row.evidence_json or []),
        "manager_user_id": row.manager_user_id,
        "manager_level": row.manager_level,
        "manager_comment": row.manager_comment,
        "hr_user_id": row.hr_user_id,
        "hr_level": row.hr_level,
        "hr_comment": row.hr_comment,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _capability_rating_out(row: CapabilityRating) -> dict[str, Any]:
    return {
        "id": row.id,
        "employee_user_id": row.employee_user_id,
        "talent_profile_id": row.talent_profile_id,
        "dimension_name": row.dimension_name,
        "level": row.level,
        "version": row.version,
        "effective": row.effective,
        "source_change_id": row.source_change_id,
        "basis": dict(row.basis_json or {}),
        "created_at": row.created_at,
    }


@router.post("/capability-rating-changes", summary="提交能力评级变更（经理，证据+评价）")
def create_capability_change(
    payload: CapabilityRatingChangeCreateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.create_capability_rating_change(db, payload, user=user)
    return ok(_capability_change_out(row))


@router.get("/capability-rating-changes", summary="能力评级变更队列（管理侧全量 / 员工本人）")
def list_capability_changes(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str | None, Query(max_length=20)] = None,
):
    rows = service.list_capability_rating_changes(db, user=user, status=status)
    return ok([_capability_change_out(row) for row in rows])


@router.post(
    "/capability-rating-changes/{change_id}/review",
    summary="HR 审核能力评级变更（通过则生成新正式评级版本）",
)
def review_capability_change(
    change_id: int,
    payload: CapabilityRatingReviewRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.review_capability_rating_change(db, change_id, payload, user=user)
    return ok(_capability_change_out(row))


@router.get("/capability-ratings", summary="正式能力评级历史（管理侧可按员工过滤 / 员工本人）")
def list_capability_ratings(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    employee_user_id: Annotated[int | None, Query(ge=1)] = None,
):
    rows = service.list_capability_ratings(db, user=user, employee_user_id=employee_user_id)
    return ok([_capability_rating_out(row) for row in rows])


# ---------------------------------------------------------------- 学习与成长

@router.put("/plan-items/{item_id}/progress", summary="员工自报学习状态")
def update_progress(
    item_id: int,
    payload: ProgressUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    record = service.update_progress(db, item_id, payload, user=user)
    return ok(
        {
            "plan_item_id": record.plan_item_id,
            "status": record.status,
            "note": record.note,
            "reported_at": record.reported_at,
            "is_self_reported": bool(record.is_self_reported),
        }
    )


@router.get("/growth", summary="成长记录（员工本人 / 管理员按员工查询）")
def growth(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[PageParams, Depends(pagination)],
    employee_user_id: int | None = Query(None),
    talent_profile_id: int | None = Query(None),
):
    rows, total = service.list_growth(
        db,
        user=user,
        employee_user_id=employee_user_id,
        talent_profile_id=talent_profile_id,
        offset=page.offset,
        limit=page.limit,
    )
    return paginated([_growth_out(row) for row in rows], total, page.page, page.page_size)
