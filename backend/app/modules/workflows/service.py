"""工作流数据模型 service（T-D14）。

职责边界：定义/版本/节点/连线的校验与落库、版本锁定、运行/步骤登记；
节点执行语义归 T-D17（执行器）、触发归 T-D18、生命周期审核归 T-D15。

版本锁定实现：已发布版本拒绝 update_draft_version（改版 = 新建更高版本草稿）；
运行实例 start_run 时钉死 version_id，之后不受任何新版本影响（验收①）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.modules.workflows.models import (
    APPROVAL_BRANCHES,
    NODE_TYPES,
    REF_KINDS,
    RUN_STATUSES,
    RUN_TERMINAL_STATUSES,
    STEP_STATUSES,
    TRIGGER_TYPES,
    VERSION_STATUSES,
    NODE_KEY_MAX,
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStep,
)

_KEY_BAD = BizError(422, "node_key 必须为小写字母开头、小写字母/数字/下划线组成且 ≤64 字符")
_MUTATE_PUBLISHED = "已发布版本不可修改（版本锁定）；请新建草稿版本"


def create_definition(
    db: Session, *, code: str, name: str, description: str | None = None
) -> WorkflowDefinition:
    """新建流程定义（code 唯一，小写蛇形）。"""
    if not _is_valid_key(code or ""):
        raise _KEY_BAD
    if not name or len(name) > 128:
        raise BizError(422, "name 必填且 ≤128 字符")
    exists = db.execute(
        select(WorkflowDefinition).where(WorkflowDefinition.code == code)
    ).scalar_one_or_none()
    if exists is not None:
        raise BizError(422, f"流程 code 已存在：{code}")
    row = WorkflowDefinition(code=code, name=name, description=description)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_draft_version(
    db: Session,
    definition_id: int,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    created_by: int | None = None,
    note: str | None = None,
) -> WorkflowDefinitionVersion:
    """新建草稿版本：整图校验（白名单/引用/连线）通过后版本号 = max+1 落库。"""
    definition = db.get(WorkflowDefinition, definition_id)
    if definition is None:
        raise BizError(404, "流程定义不存在")
    _validate_graph(nodes, edges)
    max_version = db.execute(
        select(func.max(WorkflowDefinitionVersion.version)).where(
            WorkflowDefinitionVersion.definition_id == definition_id
        )
    ).scalar()
    version = WorkflowDefinitionVersion(
        definition_id=definition_id,
        version=(max_version or 0) + 1,
        status="draft",
        note=note,
        created_by=created_by,
    )
    db.add(version)
    db.flush()
    _insert_graph(db, version.id, nodes, edges)
    db.commit()
    db.refresh(version)
    return version


def update_draft_version(
    db: Session,
    version_id: int,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> WorkflowDefinitionVersion:
    """整图替换草稿版本的节点/连线（发布版本拒绝——版本锁定）。"""
    version = _get_version(db, version_id)
    if version.status != "draft":
        raise BizError(409, _MUTATE_PUBLISHED)
    _validate_graph(nodes, edges)
    for node in db.execute(
        select(WorkflowNode).where(WorkflowNode.version_id == version_id)
    ).scalars():
        db.delete(node)
    for edge in db.execute(
        select(WorkflowEdge).where(WorkflowEdge.version_id == version_id)
    ).scalars():
        db.delete(edge)
    db.flush()
    _insert_graph(db, version_id, nodes, edges)
    db.commit()
    db.refresh(version)
    return version


def publish_version(
    db: Session, version_id: int, *, published_by: int | None = None
) -> WorkflowDefinitionVersion:
    """草稿 → 发布（≥1 节点才可发布；发布后不可变）。"""
    version = _get_version(db, version_id)
    if version.status != "draft":
        raise BizError(409, f"仅草稿可发布，当前 {version.status}")
    node_count = db.execute(
        select(func.count()).select_from(WorkflowNode).where(WorkflowNode.version_id == version_id)
    ).scalar_one()
    if node_count < 1:
        raise BizError(422, "空图不可发布（至少 1 个节点）")
    version.status = "published"
    version.published_by = published_by
    version.published_at = datetime.now()
    db.commit()
    db.refresh(version)
    return version


def deprecate_version(db: Session, version_id: int) -> WorkflowDefinitionVersion:
    """发布版本 → 废弃（运行中实例不受影响——它们钉死了自己的 version_id）。"""
    version = _get_version(db, version_id)
    if version.status != "published":
        raise BizError(409, f"仅发布版本可废弃，当前 {version.status}")
    version.status = "deprecated"
    db.commit()
    db.refresh(version)
    return version


def start_run(
    db: Session,
    *,
    definition_id: int,
    thread_id: str,
    version_id: int | None = None,
    params: dict[str, Any] | None = None,
    trigger_type: str = "manual",
    trigger_ref: str | None = None,
    created_by: int | None = None,
) -> WorkflowRun:
    """登记业务运行实例：**钉死 version_id**（版本锁定核心）。

    version_id 缺省取最新发布版本；草稿/废弃版本不可启动。
    引擎执行（thread_id → workflow_engine_runs/检查点）由 T-D17/T-D18 接线，
    本函数只负责业务登记与版本绑定。
    """
    if db.get(WorkflowDefinition, definition_id) is None:
        raise BizError(404, "流程定义不存在")
    if not thread_id or len(thread_id) > 191:
        raise BizError(422, "thread_id 必填且 ≤191 字符")
    if trigger_type not in TRIGGER_TYPES:
        raise BizError(422, "触发类型非法")
    if version_id is None:
        version = db.execute(
            select(WorkflowDefinitionVersion)
            .where(
                WorkflowDefinitionVersion.definition_id == definition_id,
                WorkflowDefinitionVersion.status == "published",
            )
            .order_by(WorkflowDefinitionVersion.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if version is None:
            raise BizError(422, "无可运行版本（先发布）")
    else:
        version = _get_version(db, version_id)
        if version.definition_id != definition_id:
            raise BizError(422, "版本不属于该流程定义")
        if version.status != "published":
            raise BizError(422, f"仅发布版本可启动运行，当前 {version.status}")
    run = WorkflowRun(
        definition_id=definition_id,
        version_id=version.id,
        thread_id=thread_id,
        trigger_type=trigger_type,
        trigger_ref=trigger_ref,
        params_json=params,
        created_by=created_by,
    )
    db.add(run)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - thread_id 唯一约束冲突
        db.rollback()
        raise BizError(409, f"thread_id 已存在：{thread_id}") from exc
    db.refresh(run)
    return run


def sync_run_status(
    db: Session, run_id: int, *, status: str, error: str | None = None
) -> WorkflowRun:
    """业务侧运行状态镜像（与引擎登记同状态机）；终态落 finished_at。"""
    if status not in RUN_STATUSES:
        raise BizError(422, "运行状态非法")
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise BizError(404, "运行实例不存在")
    run.status = status
    run.error = error
    if status in RUN_TERMINAL_STATUSES:
        run.finished_at = datetime.now()
    db.commit()
    db.refresh(run)
    return run


def record_step(
    db: Session,
    run_id: int,
    *,
    node_key: str,
    node_type: str,
    status: str,
    attempt: int = 1,
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> WorkflowRunStep:
    """落节点步骤记录：(run_id, node_key, attempt) 幂等覆盖；重试记新 attempt。"""
    if node_type not in NODE_TYPES:
        raise BizError(422, f"节点类型 {node_type} 不在白名单")
    if status not in STEP_STATUSES:
        raise BizError(422, "步骤状态非法")
    if db.get(WorkflowRun, run_id) is None:
        raise BizError(404, "运行实例不存在")
    step = db.execute(
        select(WorkflowRunStep).where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.node_key == node_key,
            WorkflowRunStep.attempt == attempt,
        )
    ).scalar_one_or_none()
    if step is None:
        step = WorkflowRunStep(run_id=run_id, node_key=node_key, node_type=node_type, attempt=attempt)
        db.add(step)
    step.node_type = node_type
    step.status = status
    step.output_json = output
    step.error = error
    if status == "running":
        step.started_at = datetime.now()
    if status in ("succeeded", "failed", "skipped"):
        step.finished_at = datetime.now()
    db.commit()
    db.refresh(step)
    return step


def get_version_detail(db: Session, version_id: int) -> dict[str, Any]:
    """定义/版本/节点/连线全量读取（可审阅；T-D15 审核页、T-D16 对账共用）。"""
    version = _get_version(db, version_id)
    definition = db.get(WorkflowDefinition, version.definition_id)
    nodes = (
        db.execute(
            select(WorkflowNode)
            .where(WorkflowNode.version_id == version_id)
            .order_by(WorkflowNode.id.asc())
        )
        .scalars()
        .all()
    )
    edges = (
        db.execute(
            select(WorkflowEdge)
            .where(WorkflowEdge.version_id == version_id)
            .order_by(WorkflowEdge.id.asc())
        )
        .scalars()
        .all()
    )
    return {
        "definition": {"id": definition.id, "code": definition.code, "name": definition.name},
        "version": {
            "id": version.id,
            "version": version.version,
            "status": version.status,
            "note": version.note,
            "published_at": version.published_at,
        },
        "nodes": [
            {
                "node_key": n.node_key,
                "name": n.name,
                "node_type": n.node_type,
                "position": n.position_json,
                "config": n.config_json,
                "inputs": n.inputs_json or [],
                "outputs": n.outputs_json or [],
            }
            for n in nodes
        ],
        "edges": [
            {
                "source": e.source_node_key,
                "target": e.target_node_key,
                "branch": e.branch or None,
                "condition": e.condition_json,
            }
            for e in edges
        ],
    }


def list_versions(db: Session, definition_id: int) -> list[WorkflowDefinitionVersion]:
    """定义全部版本（版本号倒序）。"""
    return list(
        db.execute(
            select(WorkflowDefinitionVersion)
            .where(WorkflowDefinitionVersion.definition_id == definition_id)
            .order_by(WorkflowDefinitionVersion.version.desc())
        )
        .scalars()
        .all()
    )


# ---- 内部：图校验与落库 ----


def _get_version(db: Session, version_id: int) -> WorkflowDefinitionVersion:
    version = db.get(WorkflowDefinitionVersion, version_id)
    if version is None:
        raise BizError(404, "流程版本不存在")
    return version


def _is_valid_key(key: str) -> bool:
    if not key or len(key) > NODE_KEY_MAX:
        return False
    return key[0].isalpha() and key.islower() and all(c.islower() or c.isdigit() or c == "_" for c in key)


def _validate_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """整图校验：节点白名单（验收②）、键合法/唯一、连线端点、分支规则、输入/输出引用（验收③）。

    两段式：先收集全部 node_key，再校验输入引用——node_output 允许指向图中
    任意节点（与声明顺序无关）。
    """
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise BizError(422, "nodes/edges 必须为列表")
    node_keys: set[str] = set()
    node_types: dict[str, str] = {}
    seen: set[tuple[str, str, str]] = set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {
            "key", "name", "node_type", "position", "config", "inputs", "outputs"
        }:
            raise BizError(422, "节点字段不合法（仅允许 key/name/node_type/position/config/inputs/outputs）")
        key, name, node_type = node.get("key"), node.get("name"), node.get("node_type")
        if not _is_valid_key(key or ""):
            raise _KEY_BAD
        if not name or len(str(name)) > 128:
            raise BizError(422, f"节点 {key} 的 name 必填且 ≤128 字符")
        if node_type not in NODE_TYPES:
            raise BizError(
                422,
                f"节点类型 {node_type!r} 不在白名单（{', '.join(NODE_TYPES)}），拒绝入库",
            )
        if key in node_keys:
            raise BizError(422, f"node_key 重复：{key}")
        node_keys.add(key)  # type: ignore[arg-type]
        node_types[key] = node_type  # type: ignore[assignment]

    for node in nodes:
        _validate_inputs(node.get("inputs") or [], node_keys, node.get("key"))
        _validate_outputs(node.get("outputs") or [], node.get("key"))

    for edge in edges:
        if not isinstance(edge, dict) or set(edge) - {"source", "target", "branch", "condition"}:
            raise BizError(422, "连线字段不合法（仅允许 source/target/branch/condition）")
        source, target, branch = edge.get("source"), edge.get("target"), edge.get("branch") or ""
        if source not in node_keys or target not in node_keys:
            raise BizError(422, f"连线端点不在节点集内：{source} → {target}")
        if source == target:
            raise BizError(422, f"自环连线不允许：{source}")
        if branch:
            if node_types[source] != "human_approval":
                raise BizError(422, f"仅人工审批节点使用分支出边：{source}")
            if branch not in APPROVAL_BRANCHES:
                raise BizError(422, f"审批分支仅允许 {'/'.join(APPROVAL_BRANCHES)}，得到 {branch!r}")
        elif node_types[source] == "human_approval":
            raise BizError(422, f"人工审批节点出边必须带分支（approve/reject）：{source}")
        identity = (source, target, branch)
        if identity in seen:
            raise BizError(422, f"重复连线：{source} → {target}（{branch or '默认'}）")
        seen.add(identity)


def _validate_inputs(inputs: Any, node_keys: set[str], owner_key: Any) -> None:
    """输入引用校验（验收③）：kind 白名单 + 必填字段 + node_output 指向本版本节点。"""
    if not isinstance(inputs, list):
        raise BizError(422, f"节点 {owner_key} 的 inputs 必须为列表")
    aliases: set[str] = set()
    for ref in inputs:
        if not isinstance(ref, dict) or set(ref) != {"as", "from"}:
            raise BizError(422, f"节点 {owner_key} 输入引用格式必须为 {{as, from}}")
        alias, source = ref["as"], ref["from"]
        if not alias or not isinstance(alias, str):
            raise BizError(422, f"节点 {owner_key} 输入引用 as 必填")
        if alias in aliases:
            raise BizError(422, f"节点 {owner_key} 输入引用 as 重复：{alias}")
        aliases.add(alias)
        if not isinstance(source, dict) or source.get("kind") not in REF_KINDS:
            raise BizError(422, f"节点 {owner_key} 引用 kind 必须为 {'/'.join(REF_KINDS)}")
        kind = source["kind"]
        extra = set(source) - {"kind"}
        if kind == "trigger" and extra:
            raise BizError(422, "trigger 引用不允许附加字段")
        if kind == "form_field":
            if extra - {"field"} or not source.get("field"):
                raise BizError(422, "form_field 引用必须带 field")
        if kind == "node_output":
            if extra - {"node_key", "field"} or not source.get("node_key") or not source.get("field"):
                raise BizError(422, "node_output 引用必须带 node_key 与 field")
            if source["node_key"] not in node_keys:
                raise BizError(422, f"node_output 引用指向未知节点：{source['node_key']}")
        if kind == "literal":
            if extra - {"value"} or "value" not in source:
                raise BizError(422, "literal 引用必须带 value")


def _validate_outputs(outputs: Any, owner_key: Any) -> None:
    """输出声明校验：[{as, type?, desc?}]，as 唯一非空。"""
    if not isinstance(outputs, list):
        raise BizError(422, f"节点 {owner_key} 的 outputs 必须为列表")
    aliases: set[str] = set()
    for item in outputs:
        if not isinstance(item, dict) or set(item) - {"as", "type", "desc"}:
            raise BizError(422, f"节点 {owner_key} 输出声明仅允许 as/type/desc")
        alias = item.get("as")
        if not alias or not isinstance(alias, str):
            raise BizError(422, f"节点 {owner_key} 输出声明 as 必填")
        if alias in aliases:
            raise BizError(422, f"节点 {owner_key} 输出声明 as 重复：{alias}")
        aliases.add(alias)


def _insert_graph(
    db: Session, version_id: int, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> None:
    for node in nodes:
        db.add(
            WorkflowNode(
                version_id=version_id,
                node_key=node["key"],
                name=node["name"],
                node_type=node["node_type"],
                position_json=node.get("position"),
                config_json=node.get("config"),
                inputs_json=node.get("inputs") or [],
                outputs_json=node.get("outputs") or [],
            )
        )
    for edge in edges:
        db.add(
            WorkflowEdge(
                version_id=version_id,
                source_node_key=edge["source"],
                target_node_key=edge["target"],
                branch=edge.get("branch") or "",
                condition_json=edge.get("condition"),
            )
        )
