"""Dify DSL ↔ 落库流程模型 双向映射（T-D16）。

映射约定（契约 docs/modules/D/Dify画布对接契约.md §2，DifyBridge DSL v1）：
- envelope 与 Dify 1.19.0 导出一致：{version, kind: app, app{...mode: workflow},
  workflow{graph{nodes,edges}, features, environment_variables, conversation_variables,
  rag_pipeline_variables}}（实核其 make_app_dsl / WorkflowContentDict）；
- 域节点 1:1 映射为 Dify 自定义类型节点：data.type = "ait_<node_type>"，node_key 即
  Dify node id；config/inputs/outputs 原样放 data.ait_config / ait_inputs / ait_outputs；
- 连线：branch（approve/reject）→ sourceHandle；condition_json → data.ait_condition；
  非分支出边 sourceHandle = "source"；
- 不注入合成 start/end：本仓库图无 start/end 语义（触发经 run params），1:1 映射
  保证「导出 → 导入」图恒等（对账验收②的基础）。

导入侧只接受 ait_* 节点且连线端点必须落在本图节点集内——画布上的异类节点
（llm/if-else/code 等）一律 422 拒绝并点名列出，不允许静默丢弃（验收③：画布外
改动需显式导入，异构节点须由操作者显式处理）。
"""

from __future__ import annotations

from typing import Any

import yaml

from app.core.errors import BizError
from app.modules.workflows.models import NODE_TYPES, NODE_TYPE_LABELS

# Dify 1.19.0（T-D05 锁定源码）CURRENT_APP_DSL_VERSION
DIFY_DSL_VERSION = "0.7.0"
APP_KIND = "app"
NODE_TYPE_PREFIX = "ait_"

# 导入可接受的 DSL 版本上界（本桥以 1.19.0 的 0.7.0 为基准；更高版本拒绝并提示）
MAX_ACCEPTED_DSL_VERSION = (0, 7, 0)

SOURCE_HANDLE_DEFAULT = "source"
TARGET_HANDLE = "target"

# Dify 节点外观结构字段（画布渲染用；与执行/对账语义无关）
_NODE_SHELL_KEYS = ("type", "position", "positionAbsolute", "sourcePosition",
                    "targetPosition", "selected", "zIndex")


def ait_node_type(node_type: str) -> str:
    """本仓库节点类型 → Dify data.type（ait_ 前缀）。"""
    return f"{NODE_TYPE_PREFIX}{node_type}"


def parse_ait_type(data_type: Any) -> str | None:
    """Dify data.type → 本仓库节点类型；非 ait_ 前缀返回 None。"""
    if not isinstance(data_type, str) or not data_type.startswith(NODE_TYPE_PREFIX):
        return None
    suffix = data_type[len(NODE_TYPE_PREFIX):]
    return suffix or None


def _plain(value: Any) -> Any:
    """ORM MutableDict/MutableList（dict/list 子类）→ 纯 dict/list。

    PyYAML SafeRepresenter 按精确类型查 representer，子类落到
    represent_undefined——凡进 YAML 的 JSON 列先过本函数。
    """
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def export_dsl_data(detail: dict[str, Any]) -> dict[str, Any]:
    """`service.get_version_detail` 输出 → Dify DSL envelope dict（导入/导出共用形状）。"""
    definition = detail["definition"]
    version = detail["version"]
    nodes_out: list[dict[str, Any]] = []
    for n in detail["nodes"]:
        node_type = n["node_type"]
        position = _plain(n.get("position") or {"x": 0, "y": 0})
        x, y = position.get("x", 0), position.get("y", 0)
        nodes_out.append(
            {
                "id": n["node_key"],
                "type": "custom",
                "position": {"x": x, "y": y},
                "positionAbsolute": {"x": x, "y": y},
                "sourcePosition": "right",
                "targetPosition": "left",
                "selected": False,
                "zIndex": 0,
                "data": {
                    "type": ait_node_type(node_type),
                    "title": n["name"],
                    "desc": f"{NODE_TYPE_LABELS.get(node_type, node_type)}（AIT {node_type}）",
                    "node_key": n["node_key"],
                    "ait_config": _plain(n.get("config")),
                    "ait_inputs": _plain(n.get("inputs") or []),
                    "ait_outputs": _plain(n.get("outputs") or []),
                },
            }
        )
    type_of = {n["node_key"]: ait_node_type(n["node_type"]) for n in detail["nodes"]}
    edges_out: list[dict[str, Any]] = []
    for e in detail["edges"]:
        source, target = e["source"], e["target"]
        branch = e.get("branch") or ""
        condition = _plain(e.get("condition"))
        edge_data: dict[str, Any] = {
            "sourceType": type_of.get(source, ""),
            "targetType": type_of.get(target, ""),
            "isInIteration": False,
        }
        if condition is not None:
            edge_data["ait_condition"] = condition
        edges_out.append(
            {
                "id": f"edge-{source}-{target}-{branch or 'default'}",
                "source": source,
                "target": target,
                "sourceHandle": branch or SOURCE_HANDLE_DEFAULT,
                "targetHandle": TARGET_HANDLE,
                "type": "custom",
                "zIndex": 0,
                "data": edge_data,
            }
        )
    return {
        "version": DIFY_DSL_VERSION,
        "kind": APP_KIND,
        "app": {
            "name": f"{definition['code']}_v{version['version']}",
            "mode": "workflow",
            "icon": "🤖",
            "icon_type": "emoji",
            "icon_background": "#EAECF5",
            "description": (
                f"AIT DifyBridge v1 导出：{definition['name']}（definition #{definition['id']}，"
                f"版本 #{version['id']} 的只读快照；版本锁定见工作流数据模型契约 §2）"
            ),
            "use_icon_as_answer_icon": False,
        },
        "workflow": {
            "graph": {"nodes": nodes_out, "edges": edges_out},
            # Dify WorkflowAppConfigManager 对缺失键走 validate_and_set_defaults，
            # 空 features 合法（默认值由 Dify 侧填充）
            "features": {},
            "environment_variables": [],
            "conversation_variables": [],
            "rag_pipeline_variables": [],
        },
    }


def dump_yaml(data: dict[str, Any]) -> str:
    """envelope → YAML 文本（allow_unicode 与 Dify 导出一致）。"""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def load_dsl(yaml_text: str) -> dict[str, Any]:
    """YAML 文本 → envelope dict；envelope 结构校验（422 拒绝并说明原因）。"""
    if not isinstance(yaml_text, str) or not yaml_text.strip():
        raise BizError(422, "DSL YAML 文本为空")
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise BizError(422, f"YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise BizError(422, "DSL 顶层必须是映射（dict）")
    if data.get("kind") != APP_KIND:
        raise BizError(422, f"DSL kind 必须为 {APP_KIND!r}，得到 {data.get('kind')!r}")
    app = data.get("app")
    if not isinstance(app, dict):
        raise BizError(422, "DSL 缺少顶层 app 段（非 Dify 应用 DSL）")
    if app.get("mode") != "workflow":
        raise BizError(422, f"DSL app.mode 必须为 workflow（画布对接仅支持工作流应用），得到 {app.get('mode')!r}")
    version = data.get("version")
    parsed = _parse_version(version)
    if parsed is None:
        raise BizError(422, f"DSL version 缺失或非法：{version!r}")
    if parsed > MAX_ACCEPTED_DSL_VERSION:
        raise BizError(
            422,
            f"DSL 版本 {version} 高于本桥基准 {DIFY_DSL_VERSION}（Dify 1.19.0），拒绝导入",
        )
    workflow = data.get("workflow")
    if not isinstance(workflow, dict):
        raise BizError(422, "DSL 缺少 workflow 段")
    graph = workflow.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise BizError(422, "DSL workflow.graph.nodes 缺失或非法")
    if not isinstance(graph.get("edges", []), list):
        raise BizError(422, "DSL workflow.graph.edges 必须为列表")
    return data


def dsl_to_graph_payload(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """envelope → service.create_draft_version 的 (nodes, edges) 载荷。

    严格 ait_* 语义：异类节点/游离端点 422 点名拒绝（不静默丢弃，验收③）。
    """
    graph = data["workflow"]["graph"]
    nodes: list[dict[str, Any]] = []
    for raw in graph["nodes"]:
        if not isinstance(raw, dict):
            raise BizError(422, "DSL 节点必须是映射")
        node_id = raw.get("id")
        node_data = raw.get("data")
        if not node_id or not isinstance(node_data, dict):
            raise BizError(422, f"DSL 节点缺 id 或 data：{raw!r}")
        node_type = parse_ait_type(node_data.get("type"))
        if node_type is None:
            raise BizError(
                422,
                f"节点 {node_id} 的类型 {node_data.get('type')!r} 不是本桥映射类型"
                f"（ait_ 前缀）；画布上新增的异构节点须显式处理，不静默丢弃",
            )
        if node_type not in NODE_TYPES:
            raise BizError(
                422,
                f"节点 {node_id} 的域类型 {node_type!r} 不在本仓库白名单"
                f"（{', '.join(NODE_TYPES)}）",
            )
        title = node_data.get("title")
        if not title or len(str(title)) > 128:
            raise BizError(422, f"节点 {node_id} 的 data.title 必填且 ≤128 字符")
        nodes.append(
            {
                "key": str(node_id),
                "name": str(title),
                "node_type": node_type,
                "position": raw.get("position") or {"x": 0, "y": 0},
                "config": node_data.get("ait_config"),
                "inputs": node_data.get("ait_inputs") or [],
                "outputs": node_data.get("ait_outputs") or [],
            }
        )
    keys = {n["key"] for n in nodes}
    edges: list[dict[str, Any]] = []
    for raw in graph.get("edges", []):
        if not isinstance(raw, dict):
            raise BizError(422, "DSL 连线必须是映射")
        source, target = raw.get("source"), raw.get("target")
        if source not in keys or target not in keys:
            raise BizError(
                422,
                f"连线端点不在本桥节点集内：{source!r} → {target!r}"
                "（画布上指向异构节点的连线须显式处理，不静默丢弃）",
            )
        handle = raw.get("sourceHandle") or SOURCE_HANDLE_DEFAULT
        branch = "" if handle == SOURCE_HANDLE_DEFAULT else handle
        edge_data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        edges.append(
            {
                "source": str(source),
                "target": str(target),
                "branch": branch,
                "condition": edge_data.get("ait_condition"),
            }
        )
    if not nodes:
        raise BizError(422, "DSL 图为空（无 ait_ 节点），拒绝导入")
    return nodes, edges


def normalize_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    """任一侧图 → 可比较的规范化形状（对账共用）。

    兼容两种入参形状：service 载荷用 `key`，get_version_detail 用 `node_key`。
    """
    return {
        "nodes": {
            (n.get("key") or n.get("node_key")): {
                "name": n.get("name"),
                "node_type": n.get("node_type"),
                "position": _plain(n.get("position") or {"x": 0, "y": 0}),
                "config": _plain(n.get("config")),
                "inputs": _plain(n.get("inputs") or []),
                "outputs": _plain(n.get("outputs") or []),
            }
            for n in nodes
        },
        "edges": {
            (e["source"], e["target"], e.get("branch") or ""): _plain(e.get("condition"))
            for e in edges
        },
    }


def _parse_version(version: Any) -> tuple[int, int, int] | None:
    if not isinstance(version, str):
        return None
    parts = version.split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None
