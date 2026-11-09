"""通知域业务服务（T-D03）。

口径（docs/modules/D/通知契约.md）：
- emit 幂等投递：同 `idempotency_key` 先查后插，并发兜底靠唯一约束
  （IntegrityError 回滚后返回已有行）——「同一事件重复触发不重复通知」；
- 投递语义：站内通知落库即送达（默认标记 sent）；`deliver` 钩子留给外部
  通道/测试注入，异常转 `failed` + `failure_reason`（同一行更新，不新增记录），
  `retry_failed` 重发同样不新增记录。
- 重试与 TaskRunner 的关系：TaskRunner 的任务模型（pending/running/heartbeat
  列）面向解析/筛选/匹配类任务表，通知行状态机不匹配，故失败重试采用
  「幂等键重发」语义（emit 重入 / retry_failed），不为通知改造 runner——
  外部通道（邮件/短信）启用时再评估专用投递队列，登记于契约 v1.1。
- 读取隔离（F09 验收）：personal 仅本人（recipient_user_id==user.id）；
  管理侧（can_view_all_persons）可查全量与按收件人过滤；已读仅收件人本人。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth.scope import get_account_scope
from app.core.errors import BizError
from app.modules.notifications.models import EVENT_TYPES, STATUSES, Notification

RESULT_NOT_FOUND = "资源不存在"
EVENT_TYPE_INVALID = "未登记的通知事件类型（F09 封闭清单外）"
STATUS_INVALID = "非法的通知状态过滤值"
RETRY_ONLY_FAILED = "仅 failed 状态的通知可重发"
READ_FORBIDDEN = "仅收件人本人可标记已读"

Deliver = Callable[[Session, Notification], None]


def emit(
    db: Session,
    *,
    event_type: str,
    ref_type: str,
    ref_id: int,
    recipient_user_id: int,
    idempotency_key: str,
    actor_user_id: int | None = None,
    payload: dict | None = None,
    deliver: Deliver | None = None,
) -> tuple[Notification, bool]:
    """幂等投递一条通知；返回 (通知行, 是否本次新建)。

    同幂等键重入返回已有行（created=False），不产生第二条通知。
    """
    if event_type not in EVENT_TYPES:
        raise BizError(422, EVENT_TYPE_INVALID)
    if not idempotency_key or len(idempotency_key) > 191:
        raise BizError(422, "幂等键必填且 ≤191 字符")
    if recipient_user_id is None or int(recipient_user_id) <= 0:
        raise BizError(422, "收件人 user_id 非法")
    if payload is not None and not isinstance(payload, dict):
        raise BizError(422, "payload 必须为对象")

    existing = (
        db.execute(select(Notification).where(Notification.idempotency_key == idempotency_key))
        .scalar_one_or_none()
    )
    if existing is not None:
        return existing, False

    row = Notification(
        event_type=event_type,
        ref_type=ref_type,
        ref_id=ref_id,
        actor_user_id=actor_user_id,
        recipient_user_id=int(recipient_user_id),
        payload_json=payload,
        idempotency_key=idempotency_key,
        status="pending",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # 并发同键：唯一约束拦截（F09 验收的数据库级保证），返回已有行
        db.rollback()
        existing = (
            db.execute(select(Notification).where(Notification.idempotency_key == idempotency_key))
            .scalar_one()
        )
        return existing, False

    _deliver(db, row, deliver)
    return row, True


def _deliver(db: Session, row: Notification, deliver: Deliver | None) -> Notification:
    """投递一步：默认落库即 sent；deliver 异常转 failed + reason（同一行）。"""
    try:
        if deliver is not None:
            deliver(db, row)
        row.status = "sent"
        row.sent_at = datetime.now()
        row.failure_reason = None  # 重试成功清掉上次失败原因
    except Exception as exc:  # noqa: BLE001 - 投递失败必须留痕并转 failed
        row.status = "failed"
        row.failure_reason = str(exc)[:500]
    db.commit()
    return row


def retry_failed(db: Session, notification_id: int, *, deliver: Deliver | None = None) -> Notification:
    """重发失败通知：同一行状态更新，不产生新记录（T-D03 验收④）。"""
    row = db.get(Notification, notification_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    if row.status != "failed":
        raise BizError(400, RETRY_ONLY_FAILED)
    return _deliver(db, row, deliver)


def list_for_user(
    db: Session,
    *,
    user,
    status: str | None = None,
    recipient_user_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Notification], int]:
    """按 AccountScope 读取：个人仅本人；管理侧全量或按 recipient_user_id 过滤。"""
    scope = get_account_scope(user)
    if status is not None and status not in STATUSES:
        raise BizError(422, STATUS_INVALID)
    stmt = select(Notification)
    count_stmt = select(func.count()).select_from(Notification)
    if scope.can_view_all_persons:
        if recipient_user_id is not None:
            stmt = stmt.where(Notification.recipient_user_id == recipient_user_id)
            count_stmt = count_stmt.where(Notification.recipient_user_id == recipient_user_id)
    else:
        stmt = stmt.where(Notification.recipient_user_id == user.id)
        count_stmt = count_stmt.where(Notification.recipient_user_id == user.id)
    if status is not None:
        stmt = stmt.where(Notification.status == status)
        count_stmt = count_stmt.where(Notification.status == status)
    total = db.execute(count_stmt).scalar_one() or 0
    rows = list(
        db.execute(
            stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    return rows, int(total)


def get_for_user(db: Session, *, user, notification_id: int) -> Notification:
    """详情：收件人本人或管理侧；越权/不存在统一 404/403（不泄露他人通知存在性）。"""
    row = db.get(Notification, notification_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    scope = get_account_scope(user)
    if not scope.can_view_all_persons and row.recipient_user_id != user.id:
        raise BizError(403, RESULT_NOT_FOUND)
    return row


def mark_read(db: Session, *, user, notification_id: int) -> Notification:
    """标记已读：仅收件人本人（管理侧代读不允许——已读是收件人行为）。"""
    row = db.get(Notification, notification_id)
    if row is None:
        raise BizError(404, RESULT_NOT_FOUND)
    if row.recipient_user_id != user.id:
        raise BizError(403, READ_FORBIDDEN)
    if row.status != "read":
        row.status = "read"
        row.read_at = datetime.now()
        db.commit()
    return row


def to_dict(row: Notification) -> dict[str, Any]:
    """接口序列化（schemas.NotificationItem 的来源；失败原因仅 failed 返回给有权方）。"""
    return {
        "id": row.id,
        "event_type": row.event_type,
        "ref_type": row.ref_type,
        "ref_id": row.ref_id,
        "actor_user_id": row.actor_user_id,
        "recipient_user_id": row.recipient_user_id,
        "payload": row.payload_json,
        "status": row.status,
        "failure_reason": row.failure_reason if row.status == "failed" else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "read_at": row.read_at.isoformat() if row.read_at else None,
    }
