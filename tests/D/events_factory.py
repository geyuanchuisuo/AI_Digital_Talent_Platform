"""notifications 事件工厂（T-D00f）——12 类事件 payload + 幂等键构造。

幂等键规则与 docs/modules/D/通知契约.md 一一对应：
`{event_type}:{ref_id}:{业务版本值}`。
"""
from __future__ import annotations

from typing import Any

#: 12 类封闭清单事件（顺序即 F09 封闭清单顺序）
EVENT_TYPES: list[str] = [
    "recruitment.stage_changed",
    "interview.invited",
    "interview.rescheduled",
    "interview.cancelled",
    "course.assigned",
    "course.enrollment_reviewed",
    "course.waitlist_promoted",
    "training.due_reminder",
    "training.overdue",
    "training.extension_decided",
    "workflow.approval_pending",
    "workflow.run_failed",
    "workflow.needs_human",
    "talent.gap_alert",
]

#: 各事件的 ref_type 与业务版本值字段（幂等键第三段）
_REF_TYPE: dict[str, str] = {
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


def build_event(event_type: str, ref_id: int = 1, version: Any = 1,
                recipient_user_id: int = 1, actor_user_id: int | None = None,
                payload_overrides: dict | None = None) -> dict:
    """构造一条符合通知契约的事件记录（notifications 表行形状）。"""
    if event_type not in _REF_TYPE:
        raise ValueError(f"未知事件类型：{event_type}（清单外事件一期不发）")
    payload: dict[str, Any] = {"version": version}
    if payload_overrides:
        payload.update(payload_overrides)
    return {
        "event_type": event_type,
        "ref_type": _REF_TYPE[event_type],
        "ref_id": ref_id,
        "actor_user_id": actor_user_id,
        "recipient_user_id": recipient_user_id,
        "payload_json": payload,
        "idempotency_key": f"{event_type}:{ref_id}:{version}",
        "status": "pending",
    }


def build_all_events(ref_id: int = 1) -> dict[str, dict]:
    """12 类事件各构造一条；键为 event_type。"""
    return {et: build_event(et, ref_id=ref_id) for et in EVENT_TYPES}


def idempotency_key(event_type: str, ref_id: int, version: Any) -> str:
    """契约规定的幂等键格式（供被测代码对齐）。"""
    return f"{event_type}:{ref_id}:{version}"
