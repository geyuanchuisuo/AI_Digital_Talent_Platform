"""持久化任务行的进度快照与 SSE 推送（D17）。

契约要点（测试与前端接入依据）：
- **鉴权先于流**：初始快照在返回 `StreamingResponse` 之前完成，无权限 → 403 JSON，
  不进流（与 analytics SSE 同口径）。
- **重连去重**：`Last-Event-ID` 为状态指纹（`event_id`）；仅当指纹变化时发送，
  终态在重连且已收到时**不重复发送**；断线期间中间状态不逐条重放（每次读当前
  持久化快照，不做历史队列）。
- **终态补发**：客户端未收到终态（`Last-Event-ID` 不等于终态指纹）而重连时，
  终态事件照常补发一次，随后关闭流。
- **权限同步失效**：流内每轮重新读取账号与数据范围；账号停用 / 失去权限 / 任务
  被转移时明确发 `error` 事件并关闭，不伪造进度。
- **降级**：快照读取失败（数据库不可用等）明确发 `error` 事件并关闭，不静默
  重试、不伪造进度。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.auth.scope import get_account_scope
from app.core.errors import BizError
from app.database import SessionLocal
from app.models import MatchBatch, ParseTask, ResumeFile, ScreenBatch, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
TaskKind = Literal["parse", "screen", "match"]
TERMINAL = {"parse": {"success", "failed", "duplicate_pending", "cancelled"},
            "screen": {"done", "failed"}, "match": {"done", "failed"}}

#: 轮询间隔（秒）。测试可 monkeypatch 为 0 以消除等待。
POLL_INTERVAL_SECONDS = 2.0


def snapshot(db: Session, kind: TaskKind, task_id: int, user_id: int, management: bool) -> dict[str, Any]:
    if kind == "parse":
        task = db.get(ParseTask, task_id)
        if task is None:
            raise BizError(404, "任务不存在")
        resume = db.get(ResumeFile, task.resume_file_id)
        if resume is None or (not management and resume.uploaded_by != user_id):
            raise BizError(403, "无权查看任务")
        return {"kind": kind, "task_id": task_id, "status": task.status,
                "stage": task.stage, "retries": task.retries, "error": task.error}
    if not management:
        raise BizError(403, "无权查看任务")
    model = ScreenBatch if kind == "screen" else MatchBatch
    batch = db.get(model, task_id)
    if batch is None:
        raise BizError(404, "任务不存在")
    return {"kind": kind, "task_id": task_id, "status": batch.status,
            "completed": batch.progress, "total": batch.total,
            "retry_count": batch.retry_count, "error": batch.error}


def event_id(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def format_event(data: dict[str, Any]) -> str:
    name = "done" if data["status"] in TERMINAL[data["kind"]] else "progress"
    return f"id: {event_id(data)}\nevent: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _error_event(code: str, message: str) -> str:
    """流内降级/失效信号：明确告知客户端，随后关闭流（不伪造进度）。"""
    data = {"kind": "error", "error_code": code, "message": message}
    return f"event: error\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
            # 去重：仅当状态指纹变化时发送；终态已收到（重连时 Last-Event-ID 命中）不再重发
            if fingerprint != previous:
                yield format_event(current)
                previous = fingerprint
            if terminal or await request.is_disconnected():
                return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            try:
                with SessionLocal() as db:
                    active_user = db.get(User, user.id)
                    if active_user is None or not active_user.is_active:
                        yield _error_event("account_disabled", "账号已停用")
                        return
                    current = snapshot(
                        db, kind, task_id, user.id,
                        get_account_scope(active_user).can_view_all_persons,
                    )
            except BizError as exc:
                # 权限同步失效 / 任务被转移或删除：明确告知，不静默关闭
                yield _error_event("forbidden" if exc.code == 403 else "not_found", exc.message)
                return
            except Exception:  # noqa: BLE001 数据库不可用等：明确降级，不伪造进度
                logger.exception("任务 SSE 快照失败 task_id=%s kind=%s", task_id, kind)
                yield _error_event("unavailable", "任务状态暂不可用")
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
