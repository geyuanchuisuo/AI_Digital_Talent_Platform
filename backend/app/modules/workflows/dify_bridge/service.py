"""Dify 画布对接服务面（T-D16）：导出 / 显式导入 / 对账。

全部管理侧门禁（与 T-D15 生命周期同口径，复用 service._require_management）：
- 导出：任意状态版本可导出（只读快照，发布版本导出即版本锁定的内容）；
- 导入：**显式**动作——总是落到目标定义的新草稿版本，绝不触碰已发布版本
  （版本锁定）；画布外的改动只有显式导入才进落库（验收③）；
- 对账：只读 diff 报告，不落库；两侧各自归一化后比较（验收②）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.workflows import service as wf_service
from app.modules.workflows.dify_bridge import dsl
from app.modules.workflows.models import (
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowEdge,
    WorkflowNode,
)


def export_dsl(db: Session, version_id: int, *, user) -> dict[str, Any]:
    """版本图 → Dify DSL（envelope + YAML 文本；只读）。"""
    wf_service._require_management(user)
    detail = wf_service.get_version_detail(db, version_id)
    data = dsl.export_dsl_data(detail)
    return {"data": data, "yaml": dsl.dump_yaml(data), "detail": detail}


def import_dsl(db: Session, definition_id: int, yaml_text: str, *, user) -> dict[str, Any]:
    """Dify DSL → 目标定义的新草稿版本（显式导入；已发布版本不受影响）。

    校验链：管理侧 → 定义存在 → envelope 结构 → ait_* 映射与白名单 →
    service.create_draft_version 整图校验（连线端点/分支/输入输出引用）。
    """
    wf_service._require_management(user)
    definition = db.get(WorkflowDefinition, definition_id)
    if definition is None:
        raise BizError(404, "流程定义不存在")
    data = dsl.load_dsl(yaml_text)
    nodes, edges = dsl.dsl_to_graph_payload(data)
    version = wf_service.create_draft_version(
        db,
        definition_id,
        nodes=nodes,
        edges=edges,
        user=user,
        created_by=getattr(user, "id", None),
        note="Dify DSL 显式导入（DifyBridge DSL v1）",
    )
    return {
        "definition_id": definition_id,
        "version_id": version.id,
        "version": version.version,
        "status": version.status,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def reconcile_dsl(db: Session, version_id: int, yaml_text: str, *, user) -> dict[str, Any]:
    """DSL 与落库版本对账（只读 diff 报告；不落库、不建版本）。

    报告形状：{identical, nodes: {added/removed/changed}, edges: {added/removed/changed}}；
    changed 逐字段列名（name/node_type/position/config/inputs/outputs；连线为 condition）。
    """
    wf_service._require_management(user)
    version = db.get(WorkflowDefinitionVersion, version_id)
    if version is None:
        raise BizError(404, "流程版本不存在")
    data = dsl.load_dsl(yaml_text)
    dsl_nodes, dsl_edges = dsl.dsl_to_graph_payload(data)

    stored_nodes = (
        db.execute(
            select(WorkflowNode).where(WorkflowNode.version_id == version_id)
        )
        .scalars()
        .all()
    )
    stored = dsl.normalize_graph(
        [
            {
                "key": n.node_key,
                "name": n.name,
                "node_type": n.node_type,
                "position": n.position_json,
                "config": n.config_json,
                "inputs": n.inputs_json or [],
                "outputs": n.outputs_json or [],
            }
            for n in stored_nodes
        ],
        _stored_edges(db, version_id),
    )
    incoming = dsl.normalize_graph(dsl_nodes, dsl_edges)
    return _diff(stored, incoming, version_id=version_id)


def _stored_edges(db: Session, version_id: int) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(WorkflowEdge).where(WorkflowEdge.version_id == version_id)
        )
        .scalars()
        .all()
    )
    return [
        {
            "source": e.source_node_key,
            "target": e.target_node_key,
            "branch": e.branch,
            "condition": e.condition_json,
        }
        for e in rows
    ]


def _diff(
    stored: dict[str, Any], incoming: dict[str, Any], *, version_id: int
) -> dict[str, Any]:
    """两侧规范化图的差异报告（对账验收②）。"""
    s_nodes, i_nodes = stored["nodes"], incoming["nodes"]
    s_edges, i_edges = stored["edges"], incoming["edges"]
    node_changed: list[dict[str, Any]] = []
    for key in sorted(set(s_nodes) & set(i_nodes)):
        fields = [
            f for f in ("name", "node_type", "position", "config", "inputs", "outputs")
            if s_nodes[key][f] != i_nodes[key][f]
        ]
        if fields:
            node_changed.append({"node_key": key, "fields": fields})
    edge_changed: list[dict[str, Any]] = []
    for identity in sorted(set(s_edges) & set(i_edges)):
        if s_edges[identity] != i_edges[identity]:
            edge_changed.append(
                {
                    "source": identity[0],
                    "target": identity[1],
                    "branch": identity[2] or None,
                    "fields": ["condition"],
                }
            )
    node_added = sorted(set(i_nodes) - set(s_nodes))
    node_removed = sorted(set(s_nodes) - set(i_nodes))
    edge_added = [
        {"source": i[0], "target": i[1], "branch": i[2] or None}
        for i in sorted(set(i_edges) - set(s_edges))
    ]
    edge_removed = [
        {"source": i[0], "target": i[1], "branch": i[2] or None}
        for i in sorted(set(s_edges) - set(i_edges))
    ]
    identical = not (node_added or node_removed or node_changed or edge_added or edge_removed or edge_changed)
    return {
        "version_id": version_id,
        "identical": identical,
        "nodes": {"added": node_added, "removed": node_removed, "changed": node_changed},
        "edges": {"added": edge_added, "removed": edge_removed, "changed": edge_changed},
    }
