"""招聘域路由（M3-V2.6，A08）：人才需求/项目/项目群 CRUD、申请与五阶段状态机、JD 投放。

由 main.py `include_router(router)` 注册；统一返回体与鉴权沿用 core 约定。

A08 对外分级（需求 §73）：`GET /applications` 对管理侧维持全字段，对个人侧
强制把范围收窄到 `AccountScope.person_id`（忽略前端伪造的 talent_profile_id），
且只返回已获准的对外字段；未经 HR 确认的终态结果一律不外露。完整输入快照
只经 `GET /applications/{id}/input-snapshot`（管理侧角色门禁）返回。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, require_roles
from app.core.auth.scope import account_is_management, get_account_scope
from app.core.errors import BizError
from app.core.response import ok
from app.database import get_db
from app.modules.recruitment import service as recruitment_service
from app.modules.recruitment.models import EXTERNAL_STAGES
from app.modules.recruitment.schemas import (
    PageData,
    ProjectCreate,
    ProjectDemandCreate,
    ProjectDemandItem,
    ProjectDemandUpdate,
    ProjectGroupCreate,
    ProjectGroupItem,
    ProjectItem,
)

router = APIRouter(prefix="/api/recruitment", tags=["recruitment"])

# A08：完整输入快照属内部资料（含档案/解析结果），仅管理侧可读。
_GATE_SNAPSHOT = Depends(require_roles("system_admin", "hr"))


# ---- 项目群 ----


@router.post("/project-groups", summary="创建项目群")
def create_project_group(payload: ProjectGroupCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = recruitment_service.create_project_group(
        db, name=payload.name, description=payload.description, user_id=user.id
    )
    return ok(ProjectGroupItem.model_validate(row).model_dump())


@router.get("/project-groups", summary="项目群列表")
def list_project_groups(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    rows, total = recruitment_service.list_project_groups(db, offset=(page - 1) * page_size, limit=page_size)
    return ok(
        PageData(
            items=[ProjectGroupItem.model_validate(r).model_dump() for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        ).model_dump()
    )


# ---- 项目 ----


@router.post("/projects", summary="创建项目")
def create_project(payload: ProjectCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = recruitment_service.create_project(
        db, name=payload.name, project_group_id=payload.project_group_id,
        description=payload.description, user_id=user.id,
    )
    return ok(ProjectItem.model_validate(row).model_dump())


@router.get("/projects", summary="项目列表（可按项目群过滤）")
def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    project_group_id: int | None = Query(None),
    user: CurrentUser = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    rows, total = recruitment_service.list_projects(
        db, project_group_id=project_group_id, offset=(page - 1) * page_size, limit=page_size
    )
    return ok(
        PageData(
            items=[ProjectItem.model_validate(r).model_dump() for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        ).model_dump()
    )


# ---- 人才需求 ----


@router.post("/demands", summary="创建人才需求（同岗位同项目在途需求 409）")
def create_demand(payload: ProjectDemandCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = recruitment_service.create_demand(
        db,
        {
            "position_name": payload.position_name,
            "headcount": payload.headcount,
            "project_id": payload.project_id,
            "project_group_id": payload.project_group_id,
            "job_profile_id": payload.job_profile_id,
            "expected_start_date": payload.expected_start_date,
            "priority": payload.priority,
            "remark": payload.remark,
        },
        user_id=user.id,
    )
    return ok(ProjectDemandItem.model_validate(row).model_dump())


@router.get("/demands", summary="人才需求列表（可按状态/项目过滤）")
def list_demands(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: str | None = Query(None),
    project_id: int | None = Query(None),
    user: CurrentUser = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    rows, total = recruitment_service.list_demands(
        db, status=status, project_id=project_id, offset=(page - 1) * page_size, limit=page_size
    )
    return ok(
        PageData(
            items=[ProjectDemandItem.model_validate(r).model_dump() for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        ).model_dump()
    )


@router.get("/demands/{demand_id}", summary="人才需求详情")
def get_demand(demand_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    return ok(ProjectDemandItem.model_validate(recruitment_service.get_demand(db, demand_id)).model_dump())


@router.patch("/demands/{demand_id}", summary="编辑人才需求（含状态流转白名单）")
def update_demand(
    demand_id: int,
    payload: ProjectDemandUpdate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    changes = payload.model_dump(exclude_unset=True)
    row = recruitment_service.update_demand(db, demand_id, changes, user_id=user.id)
    return ok(ProjectDemandItem.model_validate(row).model_dump())


# ---- 岗位申请与五阶段状态机 ----


@router.post("/applications", summary="创建岗位申请（同岗位在途申请 409；对外阶段按已确认映射自动更新）")
def create_application(
    payload: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)] = None,
):
    for key in ("talent_profile_id", "job_profile_id"):
        if not isinstance(payload.get(key), int):
            raise BizError(400, f"{key} 必须为整数")
    row = recruitment_service.create_application(
        db,
        talent_profile_id=payload["talent_profile_id"],
        job_profile_id=payload["job_profile_id"],
        job_profile_version=payload.get("job_profile_version", 1),
        resume_file_id=payload.get("resume_file_id"),
        user_id=user.id,
    )
    return ok(_application_out(row))


@router.get("/applications", summary="申请列表（按候选人/岗位/对外阶段过滤）")
def list_applications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    talent_profile_id: int | None = Query(None),
    job_profile_id: int | None = Query(None),
    external_stage: str | None = Query(None),
    user: CurrentUser = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    if external_stage is not None and external_stage not in EXTERNAL_STAGES:
        raise BizError(400, f"external_stage 仅支持 {' / '.join(EXTERNAL_STAGES)}")
    is_management = account_is_management(db, user)
    if not is_management:
        # 个人侧：忽略前端传入的 talent_profile_id，后端 AccountScope 是最终授权点。
        scope = get_account_scope(user)
        if scope.person_id is None:
            return ok(PageData(items=[], total=0, page=page, page_size=page_size).model_dump())
        talent_profile_id = scope.person_id
    rows, total = recruitment_service.list_applications(
        db, talent_profile_id=talent_profile_id, job_profile_id=job_profile_id,
        external_stage=external_stage, offset=(page - 1) * page_size, limit=page_size,
    )
    items = [_application_out(r) if is_management else _application_public_out(r) for r in rows]
    return ok(PageData(items=items, total=total, page=page, page_size=page_size).model_dump())


@router.patch("/applications/{application_id}/stage", summary="人工推进内部阶段（对外阶段自动映射；终态写结果类别）")
def set_application_stage(
    application_id: int,
    payload: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)] = None,
):
    stage = payload.get("internal_stage")
    if not isinstance(stage, str):
        raise BizError(400, "internal_stage 必填")
    row = recruitment_service.set_application_stage(
        db, application_id, stage, reason=payload.get("reason"), user_id=user.id
    )
    return ok(_application_out(row))


@router.post("/applications/{application_id}/result-confirm", summary="HR 确认终态结果类别（确认后才对外发布）")
def confirm_application_result(application_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)] = None):
    row = recruitment_service.confirm_application_result(db, application_id, user_id=user.id)
    return ok(_application_out(row))


@router.get(
    "/applications/{application_id}/input-snapshot",
    summary="管理侧只读：创建申请时冻结的岗位/JD/档案/简历输入快照",
)
def get_application_input_snapshot(
    application_id: int,
    user: CurrentUser,
    _gate: Annotated[tuple[str, ...], _GATE_SNAPSHOT],
    db: Annotated[Session, Depends(get_db)] = None,
):
    return ok(recruitment_service.get_application_input_snapshot(db, application_id))


def _application_public_out(row) -> dict:
    """个人侧获准字段（V2.6 §73）：只有对外阶段与经 HR 确认的结果类别。

    内部阶段、推进原因/操作人、岗位版本、简历原件 id、内部评分与评价一律不返回；
    终态结果在 HR 确认前把 result_category 置空，避免「待确认」却被读出录用/未录用。
    """
    from app.modules.recruitment.models import TERMINAL_STAGES

    published = row.result_confirmed_by is not None and row.internal_stage in TERMINAL_STAGES
    return {
        "id": row.id,
        "job_profile_id": row.job_profile_id,
        "external_stage": row.external_stage,
        "result_category": row.result_category if published else None,
        "result_published": published,
        "stage_updated_at": row.stage_updated_at.isoformat(timespec="seconds") if row.stage_updated_at else None,
    }


def _application_out(row) -> dict:
    from app.modules.recruitment.models import TERMINAL_STAGES

    return {
        "id": row.id,
        "talent_profile_id": row.talent_profile_id,
        "job_profile_id": row.job_profile_id,
        "job_profile_version": row.job_profile_version,
        "resume_file_id": row.resume_file_id,
        "internal_stage": row.internal_stage,
        "external_stage": row.external_stage,
        "result_category": row.result_category,
        "result_confirmed_by": row.result_confirmed_by,
        "result_published": row.result_confirmed_by is not None
        and row.internal_stage in TERMINAL_STAGES,
        "stage_reason": row.stage_reason,
        "stage_updated_by": row.stage_updated_by,
        "stage_updated_at": row.stage_updated_at.isoformat(timespec="seconds") if row.stage_updated_at else None,
        "closed_at": row.closed_at.isoformat(timespec="seconds") if row.closed_at else None,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
    }


# ---- JD 生成投放 ----


@router.post("/jd-publications", summary="投放 JD（草稿经授权确认后发布，渠道/版本留痕）")
def create_jd_publication(payload: dict, user: CurrentUser, db: Annotated[Session, Depends(get_db)] = None):
    for key in ("job_profile_id", "title", "content", "channel"):
        if payload.get(key) in (None, ""):
            raise BizError(400, f"{key} 必填")
    row = recruitment_service.create_jd_publication(
        db,
        job_profile_id=payload["job_profile_id"],
        job_profile_version=payload.get("job_profile_version", 1),
        title=payload["title"],
        content=payload["content"],
        channel=payload["channel"],
        user_id=user.id,
    )
    return ok(_jd_out(row))


@router.get("/jd-publications", summary="JD 投放列表（可按岗位过滤）")
def list_jd_publications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    job_profile_id: int | None = Query(None),
    user: CurrentUser = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    rows, total = recruitment_service.list_jd_publications(
        db, job_profile_id=job_profile_id, offset=(page - 1) * page_size, limit=page_size
    )
    return ok(PageData(items=[_jd_out(r) for r in rows], total=total, page=page, page_size=page_size).model_dump())


@router.post("/jd-publications/{publication_id}/withdraw", summary="撤回 JD 投放（留痕可追溯）")
def withdraw_jd_publication(publication_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)] = None):
    row = recruitment_service.withdraw_jd_publication(db, publication_id, user_id=user.id)
    return ok(_jd_out(row))


def _jd_out(row) -> dict:
    return {
        "id": row.id,
        "job_profile_id": row.job_profile_id,
        "job_profile_version": row.job_profile_version,
        "title": row.title,
        "content": row.content_snapshot_json,
        "channel": row.channel,
        "status": row.status,
        "published_by": row.published_by,
        "published_at": row.published_at.isoformat(timespec="seconds") if row.published_at else None,
        "withdrawn_by": row.withdrawn_by,
        "withdrawn_at": row.withdrawn_at.isoformat(timespec="seconds") if row.withdrawn_at else None,
    }
