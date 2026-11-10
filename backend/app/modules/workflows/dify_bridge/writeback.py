"""运行追踪回写（T-D16）：终态运行 → 已配置的 Dify 侧接收端。

设计口径（契约 docs/modules/D/Dify画布对接契约.md §5）：
- **默认关闭**：`DIFY_RUN_TRACE_URL` 环境变量缺省/空 = 不回写（skipped），
  业务流程零副作用；配置后为 best-effort——任何失败只记日志，不阻塞/不回滚业务。
- 回写挂点：`workflows.service.sync_run_status` 的终态转移后（succeeded/failed）；
  cancelled 路径（等待态/运行中取消直接 CAS）暂未挂钩，待 Dify 端点联调时统一。
- 载荷形状：ait.dify.run-trace.v1（thread/definition/version/status/steps 摘要）；
  不携带业务敏感数据（步骤 output/错误原文截断 300 字符）。
- 接收端语义（Dify 侧建什么应用/路由承接）待联调定义；传输可注入（测试用假 transport）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.workflows.models import (
    RUN_TERMINAL_STATUSES,
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowRun,
    WorkflowRunStep,
)

logger = logging.getLogger("app.dify_bridge")

RUN_TRACE_SCHEMA = "ait.dify.run-trace.v1"
_ERROR_MAX = 300

Transport = Callable[[str, dict[str, Any]], int]


def build_run_trace_payload(
    run: WorkflowRun,
    steps: list[WorkflowRunStep],
    *,
    definition_code: str,
    version_number: int,
) -> dict[str, Any]:
    """运行 + 步骤 → 回写载荷（schema ait.dify.run-trace.v1；无业务敏感数据）。"""
    return {
        "schema": RUN_TRACE_SCHEMA,
        "thread_id": run.thread_id,
        "definition": {"id": run.definition_id, "code": definition_code},
        "version": version_number,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "error": (run.error or "")[:_ERROR_MAX] or None,
        "finished_at": _iso(run.finished_at),
        "steps": [
            {
                "node_key": s.node_key,
                "node_type": s.node_type,
                "attempt": s.attempt,
                "status": s.status,
                "error": (s.error or "")[:_ERROR_MAX] or None,
                "started_at": _iso(s.started_at),
                "finished_at": _iso(s.finished_at),
            }
            for s in steps
        ],
    }


def push_run_trace(payload: dict[str, Any], *, transport: Transport | None = None) -> dict[str, Any]:
    """回写入口：未配置 URL 直接 skipped；传输失败如实返回 sent=False（不抛出）。"""
    url = (os.environ.get("DIFY_RUN_TRACE_URL", "") or "").strip()
    if not url:
        return {"skipped": "DIFY_RUN_TRACE_URL not configured"}
    sender = transport if transport is not None else _default_transport
    try:
        status = sender(url, payload)
    except Exception as exc:  # noqa: BLE001 回写失败不阻塞业务
        logger.exception("运行追踪回写失败（不阻塞业务）")
        return {"sent": False, "error": str(exc)}
    return {"sent": True, "status": status}


def notify_run_terminal(db: Session, run_id: int) -> dict[str, Any] | None:
    """终态转移后的回写钩子（由 service.sync_run_status 在 CAS 成功后调用）。

    只读重查运行/步骤/定义；未到终态或未配置回写端点时返回 None/skipped，
    任何异常吞掉记日志——回写绝不阻塞业务事务。
    """
    run = db.get(WorkflowRun, run_id)
    if run is None or run.status not in RUN_TERMINAL_STATUSES:
        return None
    if not (os.environ.get("DIFY_RUN_TRACE_URL", "") or "").strip():
        return None
    steps = (
        db.execute(
            select(WorkflowRunStep)
            .where(WorkflowRunStep.run_id == run.id)
            .order_by(WorkflowRunStep.id.asc())
        )
        .scalars()
        .all()
    )
    version = db.get(WorkflowDefinitionVersion, run.version_id)
    definition = db.get(WorkflowDefinition, run.definition_id)
    payload = build_run_trace_payload(
        run,
        list(steps),
        definition_code=definition.code if definition else f"def#{run.definition_id}",
        version_number=version.version if version else 0,
    )
    return push_run_trace(payload)


def _default_transport(url: str, payload: dict[str, Any]) -> int:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 配置方自担 URL 白名单
        return int(response.status)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None
