"""节点运行时（T-D17）：状态、输入引用解析、执行器包装与步骤记录。

状态形态（langgraph 通道）：RunState(trigger/form/vars)。
- trigger：触发负载（手动触发时为 params）；
- form：表单字段（一期手动触发暂留空，表单配置随后续任务）；
- vars：节点输出累积，按 node_key 命名空间存放（vars[node_key][as]）。

节点包装职责：每节点执行前后落 workflow_run_steps（running→succeeded/failed），
异常原样上抛——引擎统一把运行标 failed（T-D04 回退边界），失败可重试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from app.core.errors import BizError
from app.modules.workflows.models import REF_KINDS
from app.modules.workflows import service


class RunState(TypedDict, total=False):
    """图运行状态；各通道为整体替换（无 reducer），节点包装负责合并 vars。"""

    trigger: dict[str, Any]
    form: dict[str, Any]
    vars: dict[str, dict[str, Any]]


@dataclass
class NodeContext:
    """一次运行内节点的执行上下文（跨节点共享，只读）。

    session_factory：节点自开短会话（不持有请求会话，SQLite/MySQL 语义一致）；
    user_id：运行发起人——域服务的权限主体（越权调用由域服务按 AccountScope 拒绝）。
    """

    run_id: int
    thread_id: str
    session_factory: Callable[[], Any]
    user_id: int | None
    extra: dict[str, Any] = field(default_factory=dict)


def resolve_ref(state: RunState, ref: dict[str, Any] | None) -> Any:
    """按 REF_KINDS 解析单个输入引用；kind 非法或目标缺失如实报错（不静默补空）。"""
    if not isinstance(ref, dict):
        raise BizError(422, "输入引用缺失或非法")
    kind = ref.get("kind")
    if kind not in REF_KINDS:
        raise BizError(422, f"输入引用 kind {kind!r} 不在白名单 {REF_KINDS}")
    if kind == "trigger":
        return state.get("trigger") or {}
    if kind == "form_field":
        form = state.get("form") or {}
        key = str(ref.get("field") or "")
        if key not in form:
            raise BizError(422, f"表单字段缺少 {key!r}")
        return form[key]
    if kind == "node_output":
        node_key = str(ref.get("node_key") or "")
        field_name = str(ref.get("field") or "")
        upstream = (state.get("vars") or {}).get(node_key)
        if not isinstance(upstream, dict) or field_name not in upstream:
            raise BizError(422, f"上游节点 {node_key!r} 尚无输出字段 {field_name!r}")
        return upstream[field_name]
    # literal
    return ref.get("value")


def resolve_inputs(node: dict[str, Any], state: RunState) -> dict[str, Any]:
    """把节点 inputs_json（[{as, from:{...}}]）解析为 {as: value}。"""
    resolved: dict[str, Any] = {}
    for item in node.get("inputs") or []:
        alias = str(item.get("as") or "")
        if not alias:
            raise BizError(422, f"节点 {node.get('node_key')!r} 存在缺 as 的输入引用")
        resolved[alias] = resolve_ref(state, item.get("from"))
    return resolved


def load_user(session_factory: Callable[[], Any], user_id: int | None) -> Any:
    """按 id 取运行发起人（域服务权限主体）；行不存在按 403 处理（口径：无有效主体）。"""
    from app.models import User  # noqa: PLC0415 - 避免环

    if user_id is None:
        raise BizError(403, "运行缺少有效发起人，域节点拒绝执行")
    with session_factory() as db:
        user = db.get(User, user_id)
        if user is None:
            raise BizError(403, f"发起人 user_id={user_id} 不存在，域节点拒绝执行")
        db.expunge(user)
    return user


def make_node_executor(
    ctx: NodeContext, node: dict[str, Any], executor: Callable[[NodeContext, dict, dict], dict]
) -> Callable[[RunState], dict]:
    """包装单节点执行：步骤记录 + 输入解析 + vars 合并；审批节点挂起期间步骤记 waiting
    由 run_service 依据引擎 interrupt 负载落（本包装不感知挂起）。"""

    node_key = str(node.get("node_key") or "")
    node_type = str(node.get("node_type") or "")
    node_name = str(node.get("name") or node_key)

    def execute(state: RunState) -> dict:
        with ctx.session_factory() as db:
            service.record_step(
                db, ctx.run_id, node_key=node_key, node_type=node_type,
                status="running", output={"node_name": node_name},
            )
        try:
            inputs = resolve_inputs(node, state)
            outputs = executor(ctx, node, inputs) or {}
        except Exception as exc:  # noqa: BLE001 - 失败如实落步骤后原样上抛
            # BizError 带白名单状态码（如 403 越权）——码随文本落步骤，审计可辨
            text = f"[{exc.code}] {exc.message}" if isinstance(exc, BizError) else str(exc)
            with ctx.session_factory() as db:
                service.record_step(
                    db, ctx.run_id, node_key=node_key, node_type=node_type,
                    status="failed", error=text[:500],
                )
            raise
        merged_vars = {**(state.get("vars") or {}), node_key: dict(outputs)}
        with ctx.session_factory() as db:
            service.record_step(
                db, ctx.run_id, node_key=node_key, node_type=node_type,
                status="succeeded", output=outputs,
            )
        return {"vars": merged_vars}

    return execute
