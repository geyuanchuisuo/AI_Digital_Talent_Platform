"""12 类事件生产方函数（契约 v1.0 §1 / 幂等键规则 v1.1 §3）。

A/C/D 各域在自己的业务事务后调用对应函数；本模块不查各域业务表——
收件人由调用方按源对象权限解析后传入（通知不二次鉴权业务对象，
读取隔离由 service.list_for_user 的 AccountScope 兜底）。

幂等键（v1.1）：单收件人事件按契约 v1.0 原样；多收件人事件追加
`:r{recipient_user_id}`，否则同一事实的第二位收件人会被唯一约束拦截。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from app.modules.notifications.models import MULTI_RECIPIENT_EVENTS
from app.modules.notifications.service import emit

REF_BY_EVENT: dict[str, str] = {
    "recruitment.stage_changed": "application",
    "interview.invited": "interview_round",
    "interview.rescheduled": "interview_round",
    "interview.cancelled": "interview_round",
    "course.assigned": "training_plan_item",
    "course.enrollment_reviewed": "training_enrollment",
    "course.waitlist_promoted": "training_session",
    "training.due_reminder": "training_learning_record",
    "training.overdue": "training_learning_record",
    "training.extension_decided": "training_learning_record",
    "workflow.approval_pending": "workflow_run",
    "workflow.run_failed": "workflow_run",
    "workflow.needs_human": "workflow_run",
    "talent.gap_alert": "project_demand",
}


def _key(event_type: str, *parts: object) -> str:
    return ":".join([event_type, *[str(p) for p in parts if p is not None and str(p) != ""]])


def _emit(
    db: Session,
    event_type: str,
    *,
    ref_id: int,
    recipients: int | Sequence[int] | Iterable[int],
    idempotency_key: str,
    actor_user_id: int | None = None,
    payload: dict | None = None,
    deliver=None,
) -> list[tuple]:
    """单/多收件人统一入口：多收件人事件对每位收件人恒定追加 :r{id}（v1.1 规则）。"""
    ids = [recipients] if isinstance(recipients, int) else list(dict.fromkeys(int(r) for r in recipients))
    results: list[tuple] = []
    for rid in ids:
        key = idempotency_key
        if event_type in MULTI_RECIPIENT_EVENTS:
            key = f"{idempotency_key}:r{rid}"
        results.append(
            emit(
                db,
                event_type=event_type,
                ref_type=REF_BY_EVENT[event_type],
                ref_id=ref_id,
                recipient_user_id=rid,
                idempotency_key=key,
                actor_user_id=actor_user_id,
                payload=payload,
                deliver=deliver,
            )
        )
    return results


# ---- A 域：招聘（4） ----

def recruitment_stage_changed(
    db: Session, *, application_id: int, recipient_user_id: int,
    new_external_stage: str, change_seq: int, actor_user_id: int | None = None,
    result_category: str | None = None, deliver=None,
):
    """招聘阶段变化（对外五阶段）；终态附结果类别（录用/未录用）。"""
    payload = {"external_stage": new_external_stage}
    if result_category:
        payload["result_category"] = result_category
    return _emit(
        db, "recruitment.stage_changed", ref_id=application_id,
        recipients=recipient_user_id,
        idempotency_key=_key("recruitment.stage_changed", application_id, new_external_stage, change_seq),
        actor_user_id=actor_user_id, payload=payload, deliver=deliver,
    )


def interview_invited(db: Session, *, round_id: int, recipients, planned_at: str,
                      actor_user_id: int | None = None, deliver=None):
    return _emit(
        db, "interview.invited", ref_id=round_id, recipients=recipients,
        idempotency_key=_key("interview.invited", round_id, planned_at),
        actor_user_id=actor_user_id, payload={"planned_at": planned_at}, deliver=deliver,
    )


def interview_rescheduled(db: Session, *, round_id: int, recipients, new_planned_at: str,
                          actor_user_id: int | None = None, deliver=None):
    return _emit(
        db, "interview.rescheduled", ref_id=round_id, recipients=recipients,
        idempotency_key=_key("interview.rescheduled", round_id, new_planned_at),
        actor_user_id=actor_user_id, payload={"planned_at": new_planned_at}, deliver=deliver,
    )


def interview_cancelled(db: Session, *, round_id: int, recipients, planned_at: str,
                        actor_user_id: int | None = None, deliver=None):
    return _emit(
        db, "interview.cancelled", ref_id=round_id, recipients=recipients,
        idempotency_key=_key("interview.cancelled", round_id, planned_at),
        actor_user_id=actor_user_id, payload={"planned_at": planned_at}, deliver=deliver,
    )


# ---- C 域：课程（3 + 培训 3） ----

def course_assigned(db: Session, *, plan_item_id: int, recipient_user_id: int,
                    course_name: str, due_date: str | None = None,
                    actor_user_id: int | None = None, deliver=None):
    payload = {"course_name": course_name}
    if due_date:
        payload["due_date"] = due_date
    return _emit(
        db, "course.assigned", ref_id=plan_item_id, recipients=recipient_user_id,
        idempotency_key=_key("course.assigned", plan_item_id),
        actor_user_id=actor_user_id, payload=payload, deliver=deliver,
    )


def course_enrollment_reviewed(db: Session, *, enrollment_id: int, recipient_user_id: int,
                               result: str, actor_user_id: int | None = None, deliver=None):
    """报名审批结果（result：approved/rejected/cancelled 按培训域枚举）。"""
    return _emit(
        db, "course.enrollment_reviewed", ref_id=enrollment_id, recipients=recipient_user_id,
        idempotency_key=_key("course.enrollment_reviewed", enrollment_id, result),
        actor_user_id=actor_user_id, payload={"result": result}, deliver=deliver,
    )


def course_waitlist_promoted(db: Session, *, session_id: int, recipient_user_id: int,
                             seq: int, deadline: str | None = None,
                             actor_user_id: int | None = None, deliver=None):
    payload = {"confirm_deadline": deadline} if deadline else None
    return _emit(
        db, "course.waitlist_promoted", ref_id=session_id, recipients=recipient_user_id,
        idempotency_key=_key("course.waitlist_promoted", session_id, recipient_user_id, seq),
        actor_user_id=actor_user_id, payload=payload, deliver=deliver,
    )


def training_due_reminder(db: Session, *, record_id: int, recipients, due_date: str,
                          actor_user_id: int | None = None, deliver=None):
    """必修课到期提醒：员工 + 直属经理（多收件人）。"""
    return _emit(
        db, "training.due_reminder", ref_id=record_id, recipients=recipients,
        idempotency_key=_key("training.due_reminder", record_id, due_date),
        actor_user_id=actor_user_id, payload={"due_date": due_date}, deliver=deliver,
    )


def training_overdue(db: Session, *, record_id: int, recipients,
                     actor_user_id: int | None = None, deliver=None):
    """必修课逾期：员工 + 直属经理（多收件人）。"""
    return _emit(
        db, "training.overdue", ref_id=record_id, recipients=recipients,
        idempotency_key=_key("training.overdue", record_id),
        actor_user_id=actor_user_id, payload=None, deliver=deliver,
    )


def training_extension_decided(db: Session, *, record_id: int, recipients, new_due_date: str,
                               reason: str | None = None,
                               actor_user_id: int | None = None, deliver=None):
    """必修课延期决定：员工 + 直属经理；附原因（F09：管理员延期须保存原因）。"""
    payload = {"new_due_date": new_due_date}
    if reason:
        payload["reason"] = reason
    return _emit(
        db, "training.extension_decided", ref_id=record_id, recipients=recipients,
        idempotency_key=_key("training.extension_decided", record_id, new_due_date),
        actor_user_id=actor_user_id, payload=payload, deliver=deliver,
    )


# ---- D 域：工作流（3）+ 缺口预警（1） ----

def workflow_event(db: Session, *, event_type: str, run_id: int, recipients, step_no: int,
                   actor_user_id: int | None = None, detail: dict | None = None, deliver=None):
    """Agent 审批待办 / 运行失败 / 需人工介入（多收件人：审批人/指定管理员）。"""
    if event_type not in ("workflow.approval_pending", "workflow.run_failed", "workflow.needs_human"):
        raise ValueError(f"{event_type} 不是 workflow 事件")
    return _emit(
        db, event_type, ref_id=run_id, recipients=recipients,
        idempotency_key=_key(event_type, run_id, step_no),
        actor_user_id=actor_user_id, payload=detail, deliver=deliver,
    )


def talent_gap_alert(db: Session, *, demand_id: int, recipients, gap_count: int,
                     actor_user_id: int | None = None, deliver=None):
    """人才缺口预警（第 12 类）：管理员 + 业务负责人（多收件人）。"""
    return _emit(
        db, "talent.gap_alert", ref_id=demand_id, recipients=recipients,
        idempotency_key=_key("talent.gap_alert", demand_id, gap_count),
        actor_user_id=actor_user_id, payload={"gap_count": gap_count}, deliver=deliver,
    )
