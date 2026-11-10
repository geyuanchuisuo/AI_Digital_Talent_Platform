"""有状态图运行器壳（T-D04）。

职责边界：
- 引擎只负责「执行图 + 检查点 + 等待/恢复 + 运行登记」，不理解业务节点语义；
- 业务流程模型（定义/版本/节点/审批）归 T-D14；本壳供其调用；
- 与 TaskRunner 的关系：完全隔离——不同的表、不同的线程、不同的恢复语义。
  TaskRunner 心跳/恢复只碰 parse/screen/match 任务表，引擎的 waiting 态
  由检查点承载，任何恢复机制都不重置（T-D04 验收②③）。

人工等待/审批：节点内调用 langgraph `interrupt(payload)`；引擎返回 waiting +
中断信息；恢复调 `resume(thread_id, value)`（langgraph `Command(resume=...)`）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.core.workflows.engine.registry import mark_status, register_run

logger = logging.getLogger(__name__)


@dataclass
class EngineRunResult:
    """一次 start/resume 的结果；waiting 表示在人工节点挂起，需 resume 继续。"""

    status: str  # succeeded | waiting | failed
    thread_id: str
    values: dict = field(default_factory=dict)
    interrupt: Any | None = None  # waiting 时的人工节点负载（interrupt payload）
    error: str | None = None


class WorkflowEngine:
    """LangGraph 编译图 + 检查点 + 运行登记的最小封装。

    用法（T-D14 起的业务侧）：
        engine = WorkflowEngine(saver, registry_factory=SessionLocal)
        result = engine.start("parse_flow", builder, {"resume_id": 1}, thread_id=ulid())
        if result.status == "waiting":
            ...  # 通知审批人；审批动作后：
        result = engine.resume("parse_flow", builder, thread_id, {"approved": True})
    """

    def __init__(
        self,
        saver: BaseCheckpointSaver,
        *,
        registry_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._saver = saver
        self._registry_factory = registry_factory
        self._compiled: dict[str, CompiledStateGraph] = {}

    # ---- 图编译（同名缓存；builder 幂等重建无副作用） ----

    def compile(self, graph_name: str, builder: Any) -> CompiledStateGraph:
        compiled = self._compiled.get(graph_name)
        if compiled is None:
            compiled = builder.compile(checkpointer=self._saver)
            self._compiled[graph_name] = compiled
        return compiled

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _finish(self, graph_name: str, thread_id: str, invoke_error: Exception | None,
                raw: Any) -> EngineRunResult:
        """统一登记终态/等待态，返回结果对象。

        langgraph 1.x 语义：invoke 正常返回（含挂起）= channel values 字典；
        挂起时值里带 `__interrupt__` 键（Interrupt 对象列表）。以此判定 waiting。
        """
        if invoke_error is not None:
            if self._registry_factory:
                mark_status(self._registry_factory, thread_id, "failed", error=str(invoke_error)[:500])
            return EngineRunResult(
                status="failed", thread_id=thread_id, error=str(invoke_error)[:500]
            )

        values = dict(raw) if isinstance(raw, dict) else {}
        interrupts = values.pop("__interrupt__", None) or []
        if interrupts:
            payload = getattr(interrupts[0], "value", interrupts[0])
            if self._registry_factory:
                mark_status(self._registry_factory, thread_id, "waiting")
            return EngineRunResult(
                status="waiting",
                thread_id=thread_id,
                values=values,
                interrupt=payload,
            )

        if self._registry_factory:
            mark_status(self._registry_factory, thread_id, "succeeded")
        return EngineRunResult(status="succeeded", thread_id=thread_id, values=values)

    # ---- 对外 API ----

    def start(self, graph_name: str, builder: Any, input_state: dict, *,
              thread_id: str) -> EngineRunResult:
        """从头启动一次运行；thread_id 需全局唯一（ULID/UUID）。"""
        compiled = self.compile(graph_name, builder)
        if self._registry_factory:
            register_run(self._registry_factory, graph_name=graph_name, thread_id=thread_id)
        try:
            raw = compiled.invoke(input_state, self._config(thread_id))
        except Exception as exc:  # noqa: BLE001 - 引擎异常 → failed 可重试（回退边界）
            logger.exception("引擎运行失败 graph=%s thread=%s", graph_name, thread_id)
            return self._finish(graph_name, thread_id, exc, None)
        return self._finish(graph_name, thread_id, None, raw)

    def resume(self, graph_name: str, builder: Any, thread_id: str,
               resume_value: Any) -> EngineRunResult:
        """在等待节点恢复：`resume_value` 作为 interrupt() 的返回值。"""
        from langgraph.types import Command

        compiled = self.compile(graph_name, builder)
        try:
            raw = compiled.invoke(Command(resume=resume_value), self._config(thread_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception("引擎恢复失败 graph=%s thread=%s", graph_name, thread_id)
            return self._finish(graph_name, thread_id, exc, None)
        return self._finish(graph_name, thread_id, None, raw)

    def retry(self, graph_name: str, builder: Any, thread_id: str) -> EngineRunResult:
        """从失败检查点续跑（T-D18 失败阶段重试）。

        langgraph `invoke(None, config)` 语义：从最近检查点唤醒待执行任务——
        已成功节点不重执行（vars 检查点承载），只重跑失败节点及其后继；已完结
        的 thread 再续跑为空操作（不执行任何节点）。调用方须先把业务/引擎双侧
        状态转回 running（failed→running 仅经受控 retry 入口），本方法只负责
        执行与终态登记。
        """
        compiled = self.compile(graph_name, builder)
        try:
            raw = compiled.invoke(None, self._config(thread_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception("引擎重试失败 graph=%s thread=%s", graph_name, thread_id)
            return self._finish(graph_name, thread_id, exc, None)
        return self._finish(graph_name, thread_id, None, raw)

    def state(self, graph_name: str, builder: Any, thread_id: str) -> dict:
        """检查点状态（含 next 节点与 pending interrupt），供运行追踪/接管 UI。"""
        compiled = self.compile(graph_name, builder)
        state = compiled.get_state(self._config(thread_id))
        return {
            "values": dict(state.values or {}),
            "next": list(state.next or []),
            "status": getattr(state, "status", None) or "unknown",
        }
