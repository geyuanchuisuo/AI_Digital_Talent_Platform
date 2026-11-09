"""落库版本 → langgraph StateGraph 的确定性构建（T-D17）。

确定性要求：同一 version_id 构建出的图必须逐字节一致——引擎按 thread_id 恢复时
以同一构建器重建图（检查点续跑依赖节点集合/连线不变；发布版本本身不可变，T-D15）。

图名按版本与 thread_id 摘要区分运行（编译节点闭包携带本次运行身份，且登记列限 128 字符）。

一期路由语义（受控子集，与 T-D14 落库校验对齐）：
- 人工审批节点出边必须恰为 approve/reject 两支 → 条件路由 vars[key].decision；
- 普通节点单出边 → 直连；多出边须带 condition_json（{from:{kind,node,field}, equals}）
  → 条件路由；并行汇流（无条件多出边）一期不支持，如实拒绝。
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.errors import BizError

from .human_approval import approve as approval_executor
from .business import BUSINESS_EXECUTORS
from .runtime import NodeContext, RunState, make_node_executor, resolve_ref

APPROVAL_ROUTER_KEY = "decision"


def graph_name_for(version_id: int, thread_id: str) -> str:
    """图名按运行隔离编译缓存；哈希确保不超过引擎登记的 128 字符列。"""
    digest = sha256(thread_id.encode("utf-8")).hexdigest()
    return f"wf_v{version_id}_{digest}"


def _index(detail: dict[str, Any]) -> tuple[dict[str, dict], list[dict]]:
    nodes = {n["node_key"]: n for n in detail.get("nodes") or []}
    if not nodes:
        raise BizError(422, "版本无节点，无法构建图")
    edges = detail.get("edges") or []
    for edge in edges:
        for side in ("source", "target"):
            key = edge.get(side)
            if key not in nodes:
                raise BizError(422, f"连线端点 {key!r} 不在节点集合中")
    return nodes, edges


def _entry_and_exit(nodes: dict[str, dict], edges: list[dict]) -> tuple[set[str], set[str]]:
    targets = {e["target"] for e in edges}
    sources = {e["source"] for e in edges}
    entry = {k for k in nodes if k not in targets}
    exit_nodes = {k for k in nodes if k not in sources}
    if not entry:
        raise BizError(422, "图中不存在入口节点（无入边节点为空，疑似环）")
    if not exit_nodes:
        raise BizError(422, "图中不存在出口节点（无出边节点为空，疑似环）")
    return entry, exit_nodes


def _edge_router(node_key: str, edges: list[dict], nodes: dict[str, dict],
                 state: dict[str, Any]) -> str:
    """条件路由：审批节点按 decision 分支；普通节点按 condition_json 逐条求值。"""
    source = nodes[node_key]
    out = [e for e in edges if e["source"] == node_key]
    if source["node_type"] == "human_approval":
        decision = ((state.get("vars") or {}).get(node_key) or {}).get(APPROVAL_ROUTER_KEY)
        for edge in out:
            if edge.get("branch") == decision:
                return edge["target"]
        raise BizError(422, f"审批节点 {node_key!r} 无匹配分支 {decision!r} 的出边")
    for edge in out:
        condition = edge.get("condition")
        if not condition:
            continue
        expected = condition.get("equals")
        actual = resolve_ref(state, condition.get("from"))
        if actual == expected:
            return edge["target"]
    # 全不命中：走无 condition 的缺省边（若有），否则如实失败
    for edge in out:
        if not edge.get("condition"):
            return edge["target"]
    raise BizError(422, f"节点 {node_key!r} 的条件出边无一命中且无缺省边")


def build_version_graph(detail: dict[str, Any], ctx: NodeContext) -> StateGraph:
    """把 get_version_detail 的 nodes/edges 构建为可编译 StateGraph。

    节点函数 = make_node_executor 包装的域执行器；审批节点特殊（interrupt 语义）。
    """
    nodes, edges = _index(detail)
    graph: StateGraph = StateGraph(RunState)

    for key, node in nodes.items():
        node_type = node.get("node_type")
        if node_type == "human_approval":
            raw_fn = approval_executor
        elif node_type in BUSINESS_EXECUTORS:
            raw_fn = BUSINESS_EXECUTORS[node_type]
        else:
            raise BizError(422, f"节点 {key!r} 类型 {node_type!r} 无执行器")
        graph.add_node(key, make_node_executor(ctx, node, raw_fn))

    entry, exit_nodes = _entry_and_exit(nodes, edges)
    for key in entry:
        graph.add_edge(START, key)

    for key, node in nodes.items():
        out = [e for e in edges if e["source"] == key]
        if key in exit_nodes:
            continue
        if node["node_type"] == "human_approval":
            branches = {e.get("branch") for e in out}
            if branches != {"approve", "reject"}:
                raise BizError(422, f"审批节点 {key!r} 出边必须恰为 approve/reject 两支")
            graph.add_conditional_edges(
                key,
                lambda state, _k=key: _edge_router(_k, edges, nodes, state),
                [e["target"] for e in out],
            )
        elif len(out) == 1:
            if out[0].get("branch"):
                raise BizError(422, f"非审批节点 {key!r} 出边不允许 branch")
            graph.add_edge(key, out[0]["target"])
        else:
            if any(not e.get("condition") for e in out):
                raise BizError(422, f"节点 {key!r} 多出边必须逐条带 condition（并行汇流一期不支持）")
            graph.add_conditional_edges(
                key,
                lambda state, _k=key: _edge_router(_k, edges, nodes, state),
                [e["target"] for e in out],
            )

    for key in exit_nodes:
        graph.add_edge(key, END)
    return graph
