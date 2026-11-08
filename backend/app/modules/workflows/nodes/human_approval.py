"""人工审批节点（T-D17）。

机制：langgraph `interrupt()` 挂起 → 引擎返回 waiting（检查点承载，任何恢复/
心跳机制不重置——T-D04 验收②③）；审批动作经 run_service.resume_approval
（管理侧 + 四眼 + 超时校验）后以 `Command(resume=...)` 恢复。

负载（interrupt payload）即审批待办的全部信息；deadline 由发起配置换算成
绝对时间随负载下发，恢复口按墙钟拒绝过期审批（定时催办扫描归 T-D18）。

输出约定：vars[node_key] = {decision: approve|reject, approved: bool, note, by, at}；
图构建器据此在审批节点出边上做 approve/reject 条件路由。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from langgraph.types import interrupt

from app.core.errors import BizError

from .runtime import NodeContext


def approve(ctx: NodeContext, node: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """审批节点执行体：挂起等待恢复值 {approved, note?, by?, at?}。"""
    config = node.get("config") or {}
    reason = str(config.get("reason") or inputs.get("reason") or "流程需要人工审批")
    deadline_hours = config.get("deadline_hours")
    deadline: str | None = None
    if deadline_hours is not None:
        try:
            deadline = (datetime.now() + timedelta(hours=float(deadline_hours))).isoformat()
        except (TypeError, ValueError) as exc:
            raise BizError(422, f"deadline_hours 非法：{deadline_hours!r}") from exc

    payload = {
        "kind": "human_approval",
        "run_id": ctx.run_id,
        "thread_id": ctx.thread_id,
        "node_key": node.get("node_key"),
        "node_name": node.get("name"),
        "reason": reason,
        "inputs": inputs,
        "deadline": deadline,
    }
    answer = interrupt(payload)
    if not isinstance(answer, dict) or "approved" not in answer:
        raise BizError(422, "审批恢复值非法：需要 {approved: bool, note?: str}")
    approved = bool(answer.get("approved"))
    return {
        "decision": "approve" if approved else "reject",
        "approved": approved,
        "note": str(answer.get("note") or ""),
        "by": answer.get("by"),
        "at": answer.get("at") or datetime.now().isoformat(),
    }
