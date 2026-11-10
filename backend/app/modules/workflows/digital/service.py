"""数字员工服务层（T-D21 纯站内版）。

三类只读场景（daily_report/resume_patrol/todo_reminder）：
- 全部为内部数据读取（analytics 快照 / parse+screen 队列 / workflow 等待审批 +
  培训逾期），不自查业务域私有表以外的写入路径；
- 结果仅落 digital_tasks + 站内通知指定管理员（notifications.producers，
  workflow.needs_human 事件——封闭清单内"需人工介入"口径）；
- 站外外发只登记 digital_outbound_requests（pending），审批通过也不自动发送。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.notifications import producers
from app.modules.workflows.service import _require_management
from app.modules.workflows.digital.models import (
    IM_CHANNELS,
    OUTBOUND_STATUSES,
    TASK_STATUSES,
    TASK_TYPES,
    TASK_TYPE_LABELS,
    DigitalOutboundRequest,
    DigitalTask,
    MemberImAccount,
)


def require_management(user) -> None:
    _require_management(user)


# --------------------------------------------------------------------------- #
# IM 账号绑定
# --------------------------------------------------------------------------- #

def upsert_im_account(
    db: Session,
    *,
    user_id: int,
    channel: str,
    im_user_id: str,
    source: str = "manual",
    note: str | None = None,
    actor,
) -> MemberImAccount:
    """登记/更新成员 IM 账号（管理侧）。无凭据形态：manual 登记不校验外部真实性。"""
    require_management(actor)
    if channel not in IM_CHANNELS:
        raise BizError(422, f"IM 通道不在白名单：{channel}（允许 {','.join(IM_CHANNELS)}）")
    if source not in ("manual", "oauth"):
        raise BizError(422, "source 仅允许 manual/oauth")
    if not im_user_id or len(im_user_id) > 128:
        raise BizError(422, "im_user_id 必填且 ≤128 字符")

    row = db.execute(
        select(MemberImAccount).where(
            MemberImAccount.user_id == user_id, MemberImAccount.channel == channel
        )
    ).scalar_one_or_none()
    if row is None:
        row = MemberImAccount(user_id=user_id, channel=channel)
        db.add(row)
    row.im_user_id = im_user_id
    row.source = source
    row.note = note
    db.commit()
    return row


def list_im_accounts(db: Session, *, user_id: int | None = None) -> list[MemberImAccount]:
    stmt = select(MemberImAccount).order_by(MemberImAccount.id)
    if user_id is not None:
        stmt = stmt.where(MemberImAccount.user_id == user_id)
    return list(db.execute(stmt).scalars())


# --------------------------------------------------------------------------- #
# 场景一：数据日报整理（读 analytics 快照，受控统计不新造口径）
# --------------------------------------------------------------------------- #

def _collect_daily_report(db: Session) -> dict[str, Any]:
    from app.modules.analytics.models import AnalyticsQuerySnapshot  # noqa: PLC0415

    since = datetime.now() - timedelta(days=1)
    snaps = db.execute(
        select(AnalyticsQuerySnapshot)
        .where(AnalyticsQuerySnapshot.created_at >= since)
        .order_by(AnalyticsQuerySnapshot.id.desc())
        .limit(20)
    ).scalars().all()
    items = [
        {
            "snapshot_id": s.id,
            "metric_id": s.metric_id,
            "data_time": s.data_time.isoformat() if s.data_time else None,
        }
        for s in snaps
    ]
    return {"window": "last_24h", "snapshot_count": len(snaps), "snapshots": items}


# --------------------------------------------------------------------------- #
# 场景二：简历队列巡检（读 parse/screen 队列状态计数，只读）
# --------------------------------------------------------------------------- #

def _collect_resume_patrol(db: Session) -> dict[str, Any]:
    from app.models import ParseTask, ScreenBatch  # noqa: PLC0415

    parse_rows = db.execute(
        select(ParseTask.status, func.count()).group_by(ParseTask.status)
    ).all()
    screen_rows = db.execute(
        select(ScreenBatch.status, func.count()).group_by(ScreenBatch.status)
    ).all()
    parse_by_status = {status: int(n) for status, n in parse_rows}
    screen_by_status = {status: int(n) for status, n in screen_rows}
    # 巡检关注点：卡住（running 超 24h 无心跳近似——一期按状态计数呈现，不定性）
    return {
        "parse_by_status": parse_by_status,
        "screen_by_status": screen_by_status,
        "parse_failed": parse_by_status.get("failed", 0),
        "parse_pending": parse_by_status.get("pending", 0),
        "screen_running": screen_by_status.get("running", 0),
        "screen_failed": screen_by_status.get("failed", 0),
    }


# --------------------------------------------------------------------------- #
# 场景三：待办提醒（workflow 等待审批 + 培训逾期，只读计数）
# --------------------------------------------------------------------------- #

def _collect_todo_reminder(db: Session) -> dict[str, Any]:
    from datetime import date  # noqa: PLC0415

    from app.models import TrainingPlanItem  # noqa: PLC0415
    from app.modules.workflows.models import WorkflowRun  # noqa: PLC0415

    waiting_runs = (
        db.execute(
            select(func.count()).select_from(WorkflowRun).where(WorkflowRun.status == "waiting")
        ).scalar_one()
    )
    # 逾期口径与 training.emit_overdue 一致：due_date < today 且未完成的学习记录。
    # learning_records 按 (plan_item_id, employee_user_id) 唯一，逐条扫与 C 口径同源。
    from app.models import TrainingLearningRecord  # noqa: PLC0415

    today = date.today()
    overdue = 0
    rows = db.execute(
        select(TrainingPlanItem.due_date, TrainingLearningRecord.status)
        .join(
            TrainingLearningRecord,
            TrainingLearningRecord.plan_item_id == TrainingPlanItem.id,
        )
        .where(TrainingPlanItem.due_date < today)
    ).all()
    for _due, status in rows:
        if status != "completed":
            overdue += 1
    return {"workflow_waiting_approvals": int(waiting_runs), "training_overdue": int(overdue)}


_COLLECTORS = {
    "daily_report": _collect_daily_report,
    "resume_patrol": _collect_resume_patrol,
    "todo_reminder": _collect_todo_reminder,
}


# --------------------------------------------------------------------------- #
# 任务执行入口（幂等：同 key 重入返回已有行，不重复落库/通知）
# --------------------------------------------------------------------------- #

def run_task(
    db: Session,
    *,
    task_type: str,
    idempotency_key: str,
    notify_admin_ids: list[int] | None = None,
    trigger: str = "manual",
    triggered_by: int | None = None,
    actor=None,
) -> tuple[DigitalTask, bool]:
    """执行一类只读场景；结果落库 + 站内通知指定管理员（封闭清单事件）。

    幂等：同 idempotency_key 重入返回已有行（created=False），不重复执行、
    不重复通知。执行失败如实置 failed（不假成功）。
    门禁：管理侧用户（actor 必传并校验）或受控系统调度（actor=None 且
    triggered_by=None——定时扫描器等内部入口，无真人身份可校验）。
    """
    if actor is not None:
        require_management(actor)
    elif triggered_by is not None:
        # 有触发人 id 但无身份对象：不校验即放行等于裸奔，直接拒绝
        raise BizError(403, "数字员工任务须管理侧身份触发")
    if task_type not in TASK_TYPES:
        raise BizError(422, f"任务类型不在白名单：{task_type}（允许 {','.join(TASK_TYPES)}）")
    if not idempotency_key or len(idempotency_key) > 191:
        raise BizError(422, "幂等键必填且 ≤191 字符")

    existing = db.execute(
        select(DigitalTask).where(DigitalTask.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    task = DigitalTask(
        task_type=task_type,
        status="running",
        trigger=trigger,
        triggered_by=triggered_by,
        notify_admin_ids=notify_admin_ids,
        idempotency_key=idempotency_key,
        started_at=datetime.now(),
    )
    db.add(task)
    db.commit()

    try:
        result = _COLLECTORS[task_type](db)
        task.result_json = result
        task.summary = _summarize(task_type, result)
        task.status = "success"
        task.finished_at = datetime.now()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 失败如实落 failed，不吞
        task.status = "failed"
        task.error = str(exc)[:500]
        task.finished_at = datetime.now()
        db.commit()
        return task, True

    _notify_admins(db, task)
    return task, True


def _summarize(task_type: str, result: dict[str, Any]) -> str:
    if task_type == "daily_report":
        return f"近 24h 共 {result['snapshot_count']} 条问数快照"
    if task_type == "resume_patrol":
        return (
            f"解析队列 pending={result['parse_pending']} failed={result['parse_failed']}；"
            f"筛选批次 running={result['screen_running']} failed={result['screen_failed']}"
        )
    return (
        f"工作流等待审批 {result['workflow_waiting_approvals']} 条；"
        f"培训逾期 {result['training_overdue']} 条"
    )


def _notify_admins(db: Session, task: DigitalTask) -> None:
    """站内通知指定管理员（workflow.needs_human——封闭清单内"需人工介入"）。

    收件人空/通知失败不回滚任务本身（任务已 success 落库）；通知幂等键含
    task id——同任务不会重复通知。
    """
    recipients = [int(r) for r in (task.notify_admin_ids or []) if int(r) > 0]
    if not recipients:
        return
    for rid in recipients:
        try:
            producers.workflow_event(
                db,
                event_type="workflow.needs_human",
                run_id=task.id,
                recipients=[rid],
                step_no=0,
                detail={
                    "source": "digital_employee",
                    "task_type": TASK_TYPE_LABELS.get(task.task_type, task.task_type),
                    "summary": task.summary,
                },
            )
        except Exception:  # noqa: BLE001 - 通知失败留任务日志，不影响任务结果
            task.error = ((task.error or "") + f";notify {rid} failed")[:500]
            db.commit()


def require_management_or_system(triggered_by: int | None) -> None:
    """兼容保留：内部调度入口的身份说明（无门禁语义，勿用于用户路径）。"""
    if triggered_by is None:
        return


def get_task(db: Session, *, task_id: int) -> DigitalTask:
    row = db.get(DigitalTask, task_id)
    if row is None:
        raise BizError(404, "数字员工任务不存在")
    return row


def list_tasks(
    db: Session, *, task_type: str | None = None, status: str | None = None, limit: int = 50
) -> list[DigitalTask]:
    stmt = select(DigitalTask).order_by(DigitalTask.id.desc()).limit(max(1, min(limit, 200)))
    if task_type is not None:
        stmt = stmt.where(DigitalTask.task_type == task_type)
    if status is not None:
        stmt = stmt.where(DigitalTask.status == status)
    return list(db.execute(stmt).scalars())


# --------------------------------------------------------------------------- #
# 站外外发请求：只登记，不执行（验收②③核心）
# --------------------------------------------------------------------------- #

def request_outbound(
    db: Session,
    *,
    channel: str,
    target_im_user_id: str,
    content: str,
    task_id: int | None = None,
    requested_by: int | None = None,
    actor=None,
) -> DigitalOutboundRequest:
    """登记一条外发请求（pending）。**不发送**——审批通过也只置 approved，
    实际发送由人工接管（可接管红线）；通道不可用不影响登记。门禁同 run_task。"""
    if actor is not None:
        require_management(actor)
    elif requested_by is not None:
        raise BizError(403, "外发请求须管理侧身份发起")
    if channel not in IM_CHANNELS:
        raise BizError(422, f"IM 通道不在白名单：{channel}")
    if not content or not content.strip():
        raise BizError(422, "外发内容不能为空")
    if not target_im_user_id:
        raise BizError(422, "目标 IM 账号必填")
    row = DigitalOutboundRequest(
        task_id=task_id,
        channel=channel,
        target_im_user_id=target_im_user_id,
        content=content,
        status="pending",
        requested_by=requested_by,
    )
    db.add(row)
    db.commit()
    return row


def decide_outbound(
    db: Session,
    *,
    request_id: int,
    approved: bool,
    note: str | None,
    actor,
) -> DigitalOutboundRequest:
    """审批外发请求（管理侧 + 四眼：发起人不可自批）。**通过≠发送**。"""
    require_management(actor)
    row = db.get(DigitalOutboundRequest, request_id)
    if row is None:
        raise BizError(404, "外发请求不存在")
    if row.status != "pending":
        raise BizError(409, f"外发请求不在待审态，当前 {row.status}")
    if row.requested_by is not None and getattr(actor, "id", None) == row.requested_by:
        raise BizError(403, "四眼原则：发起人不可自行审批外发请求")
    row.status = "approved" if approved else "rejected"
    row.decided_by = getattr(actor, "id", None)
    row.decided_at = datetime.now()
    row.decision_note = note
    db.commit()
    return row


def mark_outbound_sent(
    db: Session, *, request_id: int, actor
) -> DigitalOutboundRequest:
    """人工接管发送后回执（approved→sent）；未经审批直接置 sent 一律拒绝。"""
    require_management(actor)
    row = db.get(DigitalOutboundRequest, request_id)
    if row is None:
        raise BizError(404, "外发请求不存在")
    if row.status != "approved":
        raise BizError(409, f"仅审批通过的外发请求可标记发送，当前 {row.status}")
    row.status = "sent"
    row.sent_at = datetime.now()
    db.commit()
    return row


def list_outbound(
    db: Session, *, status: str | None = None, limit: int = 50
) -> list[DigitalOutboundRequest]:
    stmt = (
        select(DigitalOutboundRequest)
        .order_by(DigitalOutboundRequest.id.desc())
        .limit(max(1, min(limit, 200)))
    )
    if status is not None:
        if status not in OUTBOUND_STATUSES:
            raise BizError(422, f"状态不在枚举：{status}")
        stmt = stmt.where(DigitalOutboundRequest.status == status)
    return list(db.execute(stmt).scalars())


def to_task_dict(row: DigitalTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_type": row.task_type,
        "task_type_label": TASK_TYPE_LABELS.get(row.task_type, row.task_type),
        "status": row.status,
        "trigger": row.trigger,
        "triggered_by": row.triggered_by,
        "notify_admin_ids": row.notify_admin_ids,
        "result": row.result_json,
        "summary": row.summary,
        "error": row.error,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def to_outbound_dict(row: DigitalOutboundRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "channel": row.channel,
        "target_im_user_id": row.target_im_user_id,
        "content": row.content,
        "status": row.status,
        "requested_by": row.requested_by,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


def to_account_dict(row: MemberImAccount) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "channel": row.channel,
        "im_user_id": row.im_user_id,
        "source": row.source,
        "enabled": bool(row.enabled),
        "note": row.note,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


assert TASK_STATUSES and TASK_TYPES  # 枚举自检（导入即验，防误删）
