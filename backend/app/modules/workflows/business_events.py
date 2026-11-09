"""业务事件生产方入口（D 归属）。

设计要点：
- 内部模块（当前 C 的测评完成事件）用服务身份发事件，不需要真人管理账号；
- 事件码必须登记在 `BUSINESS_EVENT_DEFINITION_CODES`，来源必须在白名单内，
  避免任意模块借此越权启动流程；
- 按事件码解析已发布流程定义；未配置或未发布时返回 `skipped`，不阻断业务；
- 审计字段 `created_by` 留空（服务发起），业务参数中含发起员工 id，便于追溯。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.models import WorkflowDefinition

# 业务事件码 → 流程定义 code（D 负责发布对应定义的 published 版本）
BUSINESS_EVENT_DEFINITION_CODES: dict[str, str] = {
    "employee_assessment_completed": "employee_assessment_analysis",
}

# 允许调用内部事件入口的来源（增删需 D 维护者审批）
INTERNAL_EVENT_SOURCES = frozenset({"C.assessments"})


def _skip(reason: str, *, event_code: str) -> dict[str, Any]:
    return {"skipped": True, "reason": reason, "event_code": event_code}


def emit_business_event(
    db: Session,
    *,
    event_code: str,
    event_id: str,
    params: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    """内部生产方发布业务事件；返回 run 或 skipped 结果。"""
    if source not in INTERNAL_EVENT_SOURCES:
        raise BizError(403, f"来源 {source} 未登记为业务事件生产方")
    definition_code = BUSINESS_EVENT_DEFINITION_CODES.get(event_code)
    if not definition_code:
        raise BizError(422, f"未登记的业务事件码：{event_code}")
    definition = db.execute(
        select(WorkflowDefinition).where(WorkflowDefinition.code == definition_code)
    ).scalar_one_or_none()
    if definition is None:
        return _skip("definition_not_found", event_code=event_code)
    # 延迟导入：运行编排依赖工作流引擎（langgraph），只在实际启动运行时加载
    from app.modules.workflows import run_service  # noqa: PLC0415
    try:
        return run_service.start_event_run_for_service(
            db,
            definition_id=definition.id,
            event_type=event_code,
            event_id=event_id,
            params=params,
            source=source,
        )
    except BizError as exc:
        # 定义存在但无已发布版本：跳过，不阻断业务提交
        if exc.code == 422 and "可运行版本" in str(getattr(exc, "message", "")):
            return _skip("definition_not_published", event_code=event_code)
        raise
