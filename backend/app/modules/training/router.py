"""C 培训成长域路由（prefix `/api/training`）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination
from app.core.response import ok, paginated
from app.database import get_db
from app.modules.training import service
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
    PlanUpdateRequest,
    ProgressUpdateRequest,
)

router = APIRouter(prefix="/api/training", tags=["training"])


def _course_out(row: TrainingCourse) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "material_url": row.material_url,
        "dimension_names": list(row.dimension_names_json or []),
        "status": row.status,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _item_out(item: TrainingPlanItem, record: TrainingLearningRecord | None = None) -> dict[str, Any]:
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
            "status": record.status if record else "not_started",
            "note": record.note if record else None,
            "reported_at": record.reported_at if record else None,
            "is_self_reported": bool(record.is_self_reported) if record else True,
        },
    }
    return data


def _plan_out(
    plan: TrainingPlan,
    items: list[TrainingPlanItem] | None = None,
    *,
    records: dict[int, TrainingLearningRecord] | None = None,
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
        data["items"] = [_item_out(item, records.get(item.id)) for item in items]
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
    return ok(
        {
            "items": [
                _plan_out(
                    item["plan"],
                    item["items"],
                    records=service.learning_records(db, item["items"], user.id),
                )
                for item in rows
            ],
            "total": len(rows),
        }
    )


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
