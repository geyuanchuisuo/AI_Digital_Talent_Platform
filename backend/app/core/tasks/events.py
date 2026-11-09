"""持久化任务行的进度快照与 SSE 推送。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.auth.scope import get_account_scope
from app.core.errors import BizError
from app.database import SessionLocal
from app.models import MatchBatch, ParseTask, ResumeFile, ScreenBatch, User

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
TaskKind = Literal["parse", "screen", "match"]
TERMINAL = {"parse": {"success", "failed", "duplicate_pending", "cancelled"},
            "screen": {"done", "failed"}, "match": {"done", "failed"}}


def snapshot(db: Session, kind: TaskKind, task_id: int, user_id: int, management: bool) -> dict[str, Any]:
    if kind == "parse":
        task = db.get(ParseTask, task_id)
        if task is None:
            raise BizError(404, "任务不存在")
        resume = db.get(ResumeFile, task.resume_file_id)
        if resume is None or (not management and resume.uploaded_by != user_id):
            raise BizError(403, "无权查看任务")
        return {"kind": kind, "task_id": task_id, "status": task.status,
                "stage": task.stage, "retries": task.retries}
    if not management:
        raise BizError(403, "无权查看任务")
    model = ScreenBatch if kind == "screen" else MatchBatch
    batch = db.get(model, task_id)
    if batch is None:
        raise BizError(404, "任务不存在")
    return {"kind": kind, "task_id": task_id, "status": batch.status,
            "completed": batch.progress, "total": batch.total, "retry_count": batch.retry_count}


def event_id(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def format_event(data: dict[str, Any]) -> str:
    name = "done" if data["status"] in TERMINAL[data["kind"]] else "progress"
    return f"id: {event_id(data)}\nevent: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/{task_id}/events", summary="任务状态 SSE（按真实持久化快照）")
async def task_events(
    task_id: int,
    user: CurrentUser,
    request: Request,
    kind: TaskKind = Query(..., description="parse/screen/match，避免三张表主键歧义"),
    last_event_id: str | None = Header(default=None),
):
    management = get_account_scope(user).can_view_all_persons
    with SessionLocal() as db:
        initial = snapshot(db, kind, task_id, user.id, management)

    async def stream():
        current = initial
        previous = last_event_id
        while True:
            fingerprint = event_id(current)
            terminal = current["status"] in TERMINAL[kind]
            if fingerprint != previous or terminal:
                yield format_event(current)
                previous = fingerprint
            if terminal or await request.is_disconnected():
                return
            await asyncio.sleep(2)
            with SessionLocal() as db:
                active_user = db.get(User, user.id)
                if active_user is None or not active_user.is_active:
                    return
                current = snapshot(db, kind, task_id, user.id, get_account_scope(active_user).can_view_all_persons)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
