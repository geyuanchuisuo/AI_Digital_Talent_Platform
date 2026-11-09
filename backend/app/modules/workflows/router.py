"""工作流域路由（T-D15）：受控表单/模板 → 提交 → 管理侧审核发布/停用/回滚。

由 main.py `include_router(router)` 注册。权限口径（契约 §5）：
- 定义/版本查看、创建、修改、提交：管理侧（can_view_all_persons）；
- 审核（通过/驳回）：管理侧 + 四眼原则（提交人不可自行审核，403）；
- 停用/回滚：管理侧。
service 直连的 publish_version/deprecate_version 不经 router 暴露（受控路径唯一）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.errors import BizError
from app.core.response import ok
from app.database import get_db
from app.modules.workflows import run_service, service
from app.modules.workflows.models import (
    NODE_TYPE_LABELS,
    NODE_TYPES,
    STEP_STATUSES,
    TRIGGER_TYPES,
    VERSION_STATUSES,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("/meta/node-types", summary="节点白名单与状态枚举（前端编辑器/审核页元数据）")
def node_type_meta():
    return ok(
        {
            "node_types": [{"type": t, "label": NODE_TYPE_LABELS[t]} for t in NODE_TYPES],
            "version_statuses": list(VERSION_STATUSES),
            "trigger_types": list(TRIGGER_TYPES),
            "step_statuses": list(STEP_STATUSES),
        }
    )


@router.get("/definitions", summary="流程定义列表（管理侧）")
def list_definitions(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_management(user)
    rows = service.list_definitions(db)
    return ok(
        [
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "description": r.description,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


@router.post("/definitions", summary="新建流程定义（管理侧）")
def create_definition(
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_management(user)
    row = service.create_definition(
        db,
        code=str(body.get("code", "")),
        name=str(body.get("name", "")),
        description=body.get("description"),
        user=user,
    )
    return ok({"id": row.id, "code": row.code, "name": row.name})


@router.get("/definitions/{definition_id}/versions", summary="定义版本列表（版本号倒序，管理侧）")
def list_versions(
    definition_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_management(user)
    rows = service.list_versions(db, definition_id)
    return ok(
        [
            {
                "id": r.id,
                "version": r.version,
                "status": r.status,
                "note": r.note,
                "submitted_by": r.submitted_by,
                "submitted_at": r.submitted_at,
                "review_note": r.review_note,
                "published_at": r.published_at,
            }
            for r in rows
        ]
    )


@router.post("/definitions/{definition_id}/versions", summary="新建草稿版本（整图校验，管理侧）")
def create_draft_version(
    definition_id: int,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_management(user)
    version = service.create_draft_version(
        db,
        definition_id,
        nodes=body.get("nodes") or [],
        edges=body.get("edges") or [],
        user=user,
        created_by=user.id,
        note=body.get("note"),
    )
    return ok({"id": version.id, "version": version.version, "status": version.status})


@router.get("/versions/{version_id}", summary="定义/版本/节点/连线全量读取（可审阅）")
def get_version_detail(
    version_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_management(user)
    return ok(service.get_version_detail(db, version_id))


@router.put("/versions/{version_id}/graph", summary="整图替换草稿节点/连线（发布版本拒绝）")
def update_draft_graph(
    version_id: int,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_management(user)
    version = service.update_draft_version(
        db, version_id, nodes=body.get("nodes") or [], edges=body.get("edges") or [], user=user
    )
    return ok({"id": version.id, "version": version.version, "status": version.status})


@router.post("/versions/{version_id}/submit", summary="提交审核（管理侧；空图拒绝）")
def submit_version(
    version_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    version = service.submit_version(db, version_id, user=user)
    return ok({"id": version.id, "status": version.status, "submitted_by": version.submitted_by})


@router.post("/versions/{version_id}/review", summary="审核：approve=true 发布 / false 驳回退草稿（四眼原则）")
def review_version(
    version_id: int,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    version = service.review_version(
        db,
        version_id,
        user=user,
        approve=bool(body.get("approve")),
        note=body.get("note"),
    )
    return ok({"id": version.id, "status": version.status, "review_note": version.review_note})


@router.post("/versions/{version_id}/deactivate", summary="停用发布版本（运行中实例不受影响）")
def deactivate_version(
    version_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    version = service.deactivate_published_version(db, version_id, user=user)
    return ok({"id": version.id, "status": version.status})


@router.post("/definitions/{definition_id}/rollback", summary="回滚到目标版本（目标重新成为唯一发布版）")
def rollback_definition(
    definition_id: int,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    target = body.get("target_version_id")
    if not isinstance(target, int):
        raise BizError(422, "target_version_id 必填（整数）")
    version = service.rollback_definition(
        db, definition_id, target_version_id=target, user=user
    )
    return ok({"id": version.id, "version": version.version, "status": version.status})


# ---- 运行（T-D17）：手动触发/审批恢复/追踪；事件与定时触发归 T-D18 ----

@router.post("/runs", summary="手动触发运行（管理侧；钉死最新发布版本）")
def start_run(
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition_id = body.get("definition_id")
    if not isinstance(definition_id, int):
        raise BizError(422, "definition_id 必填（整数）")
    params = body.get("params")
    if params is not None and not isinstance(params, dict):
        raise BizError(422, "params 必须为对象")
    outcome = run_service.start_manual_run(db, definition_id=definition_id, params=params, user=user)
    run, result = outcome["run"], outcome["result"]
    return ok(
        {
            "run_id": run.id,
            "thread_id": run.thread_id,
            "version_id": run.version_id,
            "status": run.status,
            "engine_status": result.status,
            "interrupt": result.interrupt if result.status == "waiting" else None,
            "error": result.error,
        }
    )


@router.get("/runs", summary="运行实例列表（管理侧，可按定义/状态过滤）")
def list_runs(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    definition_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
):
    service.require_management(user)
    rows = run_service.list_runs(db, definition_id=definition_id, status=status)
    return ok(
        [
            {
                "id": r.id,
                "definition_id": r.definition_id,
                "version_id": r.version_id,
                "thread_id": r.thread_id,
                "trigger_type": r.trigger_type,
                "status": r.status,
                "error": r.error,
                "created_by": r.created_by,
                "created_at": r.created_at,
                "finished_at": r.finished_at,
            }
            for r in rows
        ]
    )


@router.get("/runs/{thread_id}", summary="运行详情（含步骤记录；等待态附引擎检查点状态）")
def run_detail(
    thread_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    service.require_management(user)
    return ok(run_service.run_detail(db, thread_id))


@router.post("/runs/{thread_id}/approval", summary="审批挂起节点：approve/reject（管理侧+四眼+超时校验）")
def approve_run(
    thread_id: str,
    body: dict,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    if "approved" not in body or not isinstance(body.get("approved"), bool):
        raise BizError(422, "approved 必填（布尔）")
    outcome = run_service.resume_approval(
        db,
        thread_id=thread_id,
        approved=bool(body["approved"]),
        note=body.get("note"),
        user=user,
    )
    run, result = outcome["run"], outcome["result"]
    return ok(
        {
            "run_id": run.id,
            "thread_id": run.thread_id,
            "status": run.status,
            "engine_status": result.status,
            "interrupt": result.interrupt if result.status == "waiting" else None,
            "error": result.error,
        }
    )


@router.post("/runs/{thread_id}/cancel", summary="取消人工等待中的运行（管理侧）")
def cancel_run(
    thread_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    run = run_service.cancel_waiting_run(db, thread_id=thread_id, user=user)
    return ok({"run_id": run.id, "thread_id": run.thread_id, "status": run.status})


@router.post("/runs/{thread_id}/cancel-running", summary="取消运行中的运行（管理侧；协作式中断，执行中节点不打断）")
def cancel_running(
    thread_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    run = run_service.cancel_running_run(db, thread_id=thread_id, user=user)
    return ok({"run_id": run.id, "thread_id": run.thread_id, "status": run.status})


@router.post("/runs/{thread_id}/retry", summary="失败阶段重试（管理侧；从失败检查点续跑，已成功节点不重执行）")
def retry_run(
    thread_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    outcome = run_service.retry_failed_run(db, thread_id=thread_id, user=user)
    run, result = outcome["run"], outcome["result"]
    return ok(
        {
            "run_id": run.id,
            "thread_id": run.thread_id,
            "status": run.status,
            "engine_status": result.status,
            "interrupt": result.interrupt if result.status == "waiting" else None,
            "error": result.error,
        }
    )
