"""数字员工路由（T-D21 纯站内版）。全部管理侧；挂 workflows 既有 /api/workflows 前缀。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db
from app.core.response import ok

from . import service
from .models import TASK_TYPES

router = APIRouter(prefix="/api/workflows/digital", tags=["digital-employee"])


class ImAccountUpsert(BaseModel):
    user_id: int
    channel: str
    im_user_id: str = Field(min_length=1, max_length=128)
    source: str = "manual"
    note: str | None = None


class TaskRunRequest(BaseModel):
    task_type: str
    idempotency_key: str = Field(min_length=1, max_length=191)
    notify_admin_ids: list[int] | None = None
    trigger: str = "manual"


class OutboundRequest(BaseModel):
    channel: str
    target_im_user_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    task_id: int | None = None


class OutboundDecision(BaseModel):
    approved: bool
    note: str | None = None


# ---- IM 账号绑定 ----

@router.get("/im-accounts", summary="IM 账号绑定列表（管理侧）")
def list_im_accounts(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    user_id: int | None = None,
):
    service.require_management(user)
    rows = service.list_im_accounts(db, user_id=user_id)
    return ok({"items": [service.to_account_dict(r) for r in rows]})


@router.post("/im-accounts", summary="登记/更新成员 IM 账号（管理侧，manual 无凭据形态）")
def upsert_im_account(payload: ImAccountUpsert, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = service.upsert_im_account(
        db,
        user_id=payload.user_id,
        channel=payload.channel,
        im_user_id=payload.im_user_id,
        source=payload.source,
        note=payload.note,
        actor=user,
    )
    return ok(service.to_account_dict(row))


# ---- 三类只读任务 ----

@router.post("/tasks/run", summary="执行数字员工任务（三类只读场景；幂等；结果仅入库+站内通知）")
def run_task(payload: TaskRunRequest, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    task, created = service.run_task(
        db,
        task_type=payload.task_type,
        idempotency_key=payload.idempotency_key,
        notify_admin_ids=payload.notify_admin_ids,
        trigger=payload.trigger,
        triggered_by=user.id,
        actor=user,
    )
    return ok({**service.to_task_dict(task), "created": created})


@router.get("/tasks", summary="任务列表（管理侧）")
def list_tasks(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    task_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
):
    service.require_management(user)
    rows = service.list_tasks(db, task_type=task_type, status=status, limit=limit)
    return ok({"items": [service.to_task_dict(r) for r in rows], "task_types": list(TASK_TYPES)})


@router.get("/tasks/{task_id}", summary="任务详情（管理侧）")
def get_task(task_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    service.require_management(user)
    return ok(service.to_task_dict(service.get_task(db, task_id=task_id)))


# ---- 站外外发请求（只登记，不执行） ----

@router.post("/outbound", summary="登记站外外发请求（pending；不发送）")
def request_outbound(payload: OutboundRequest, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = service.request_outbound(
        db,
        channel=payload.channel,
        target_im_user_id=payload.target_im_user_id,
        content=payload.content,
        task_id=payload.task_id,
        requested_by=user.id,
        actor=user,
    )
    return ok(service.to_outbound_dict(row))


@router.get("/outbound", summary="外发请求列表（管理侧）")
def list_outbound(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    limit: int = 50,
):
    service.require_management(user)
    rows = service.list_outbound(db, status=status, limit=limit)
    return ok({"items": [service.to_outbound_dict(r) for r in rows]})


@router.post("/outbound/{request_id}/decision", summary="审批外发请求（管理侧+四眼；通过≠发送）")
def decide_outbound(request_id: int, payload: OutboundDecision, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = service.decide_outbound(
        db, request_id=request_id, approved=payload.approved, note=payload.note, actor=user
    )
    return ok(service.to_outbound_dict(row))


@router.post("/outbound/{request_id}/mark-sent", summary="人工接管发送回执（仅 approved 可置 sent）")
def mark_outbound_sent(request_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = service.mark_outbound_sent(db, request_id=request_id, actor=user)
    return ok(service.to_outbound_dict(row))
