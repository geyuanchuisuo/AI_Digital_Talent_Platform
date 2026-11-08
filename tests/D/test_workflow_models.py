"""工作流流程数据模型用例（T-D14 验收①②③）。

①版本锁定：运行实例钉死 version_id，新版本/废弃不影响运行中实例，发布版本不可改；
②节点白名单：五类任务 + 人工审批，白名单外拒绝（建版/落步骤两处把关）；
③输入/输出引用落库：四种来源 schema 校验 + node_output 指向本图节点。
运行：pytest tests/D/test_workflow_models.py -v（SQLite 文件库，无外部依赖）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.errors import BizError
from app.models import (
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStep,
)
from app.modules.workflows.models import NODE_TYPES
from app.modules.workflows import service

# conftest 自动加载（SQLite 编译规则）；本文件全部同步用例

WF_TABLES = (
    WorkflowDefinition.__table__,
    WorkflowDefinitionVersion.__table__,
    WorkflowNode.__table__,
    WorkflowEdge.__table__,
    WorkflowRun.__table__,
    WorkflowRunStep.__table__,
)


@pytest.fixture()
def db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'wf_models.db'}")
    WorkflowDefinition.__table__.metadata.create_all(engine, tables=WF_TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _parse_approval_graph() -> tuple[list[dict], list[dict]]:
    """样例图：解析 → 人工审批（approve/reject 两路）→ 匹配 / 兜底分析。"""
    nodes = [
        {
            "key": "parse_1",
            "name": "解析简历",
            "node_type": "profile_parse",
            "position": {"x": 80, "y": 120},
            "inputs": [{"as": "resume_id", "from": {"kind": "trigger"}}],
            "outputs": [{"as": "profile_id", "type": "int"}],
        },
        {
            "key": "approval_1",
            "name": "结果复核",
            "node_type": "human_approval",
            "inputs": [
                {"as": "profile", "from": {"kind": "node_output", "node_key": "parse_1", "field": "profile_id"}},
                {"as": "min_score", "from": {"kind": "form_field", "field": "min_score"}},
                {"as": "threshold", "from": {"kind": "literal", "value": 60}},
            ],
            "outputs": [{"as": "approved", "type": "bool"}],
        },
        {
            "key": "match_1",
            "name": "岗位匹配",
            "node_type": "job_match",
            "config": {"top_k": 10},
            "inputs": [{"as": "profile_id", "from": {"kind": "node_output", "node_key": "parse_1", "field": "profile_id"}}],
            "outputs": [{"as": "matches", "type": "array"}],
        },
        {
            "key": "analysis_1",
            "name": "落选分析",
            "node_type": "data_analysis",
        },
    ]
    edges = [
        {"source": "parse_1", "target": "approval_1"},
        {"source": "approval_1", "target": "match_1", "branch": "approve"},
        {"source": "approval_1", "target": "analysis_1", "branch": "reject"},
    ]
    return nodes, edges


def _make_definition(db, code: str = "hire_review") -> WorkflowDefinition:
    return service.create_definition(db, code=code, name="招聘复核流程")


def _publish_graph(db, definition_id: int, nodes=None, edges=None) -> WorkflowDefinitionVersion:
    if nodes is None:
        nodes, edges = _parse_approval_graph()
    version = service.create_draft_version(db, definition_id, nodes=nodes, edges=edges or [])
    return service.publish_version(db, version.id)


def _node_count_for_definition(definition_id: int):
    return (
        select(func.count())
        .select_from(WorkflowNode)
        .join(WorkflowDefinitionVersion, WorkflowDefinitionVersion.id == WorkflowNode.version_id)
        .where(WorkflowDefinitionVersion.definition_id == definition_id)
    )


# ---- 验收②：节点白名单 ----


@pytest.mark.parametrize("node_type", NODE_TYPES)
def test_whitelisted_node_types_accepted(db, node_type):
    definition = _make_definition(db, f"wf_{node_type.replace('_', '')}")
    version = service.create_draft_version(
        db,
        definition.id,
        nodes=[{"key": "n1", "name": "节点", "node_type": node_type}],
        edges=[],
    )
    rows = db.execute(select(WorkflowNode).where(WorkflowNode.version_id == version.id)).scalars().all()
    assert len(rows) == 1 and rows[0].node_type == node_type


def test_unknown_node_type_rejected_everywhere(db):
    definition = _make_definition(db)
    # 建版本时拒绝（验收②核心）
    with pytest.raises(BizError) as excinfo:
        service.create_draft_version(
            db,
            definition.id,
            nodes=[{"key": "magic", "name": "魔法节点", "node_type": "llm_magic"}],
            edges=[],
        )
    assert excinfo.value.code == 422 and "白名单" in excinfo.value.message
    assert db.execute(_node_count_for_definition(definition.id)).scalar() == 0
    # 落步骤时同样拒绝（执行器侧第二道关）
    _publish_graph(db, definition.id)
    run = service.start_run(db, definition_id=definition.id, thread_id="th-wl")
    with pytest.raises(BizError) as step_exc:
        service.record_step(db, run.id, node_key="x", node_type="llm_magic", status="running")
    assert "白名单" in step_exc.value.message


# ---- 验收③：输入/输出引用落库 ----


def test_input_output_refs_persisted_and_reviewable(db):
    definition = _make_definition(db)
    nodes, edges = _parse_approval_graph()
    version = service.create_draft_version(db, definition.id, nodes=nodes, edges=edges)

    detail = service.get_version_detail(db, version.id)
    assert detail["definition"]["code"] == "hire_review"
    assert detail["version"]["status"] == "draft"
    by_key = {n["node_key"]: n for n in detail["nodes"]}
    # 四种引用来源全部落库可读（验收③）
    inputs = {i["as"]: i["from"] for i in by_key["approval_1"]["inputs"]}
    assert inputs["profile"] == {"kind": "node_output", "node_key": "parse_1", "field": "profile_id"}
    assert inputs["min_score"] == {"kind": "form_field", "field": "min_score"}
    assert inputs["threshold"] == {"kind": "literal", "value": 60}
    assert by_key["parse_1"]["inputs"][0]["from"] == {"kind": "trigger"}
    assert by_key["parse_1"]["outputs"] == [{"as": "profile_id", "type": "int"}]
    # 连线（含审批分支）落库可读
    assert {e["source"] for e in detail["edges"]} == {"parse_1", "approval_1"}
    branches = {(e["source"], e["target"]): e["branch"] for e in detail["edges"]}
    assert branches[("approval_1", "match_1")] == "approve"
    assert branches[("approval_1", "analysis_1")] == "reject"


def test_bad_refs_rejected(db):
    definition = _make_definition(db)

    def try_nodes(inputs=None, outputs=None):
        service.create_draft_version(
            db,
            definition.id,
            nodes=[
                {
                    "key": "n1",
                    "name": "节点",
                    "node_type": "data_analysis",
                    "inputs": inputs or [],
                    "outputs": outputs or [],
                }
            ],
            edges=[],
        )

    with pytest.raises(BizError, match="未知节点"):
        try_nodes(inputs=[{"as": "x", "from": {"kind": "node_output", "node_key": "ghost", "field": "f"}}])
    with pytest.raises(BizError, match="form_field"):
        try_nodes(inputs=[{"as": "x", "from": {"kind": "form_field"}}])
    with pytest.raises(BizError, match="literal"):
        try_nodes(inputs=[{"as": "x", "from": {"kind": "literal"}}])
    with pytest.raises(BizError, match="不允许附加字段"):
        try_nodes(inputs=[{"as": "x", "from": {"kind": "trigger", "extra": 1}}])
    with pytest.raises(BizError, match="kind"):
        try_nodes(inputs=[{"as": "x", "from": {"kind": "upstream_magic"}}])
    with pytest.raises(BizError, match="重复"):
        try_nodes(outputs=[{"as": "a"}, {"as": "a"}])
    with pytest.raises(BizError, match="as 必填"):
        try_nodes(outputs=[{"as": ""}])
    # 前向引用合法：node_output 可指向图中任意节点（与声明顺序无关）
    version = service.create_draft_version(
        db,
        definition.id,
        nodes=[
            {
                "key": "early",
                "name": "先定义",
                "node_type": "data_analysis",
                "inputs": [{"as": "later_out", "from": {"kind": "node_output", "node_key": "late", "field": "v"}}],
            },
            {"key": "late", "name": "后定义", "node_type": "data_analysis"},
        ],
        edges=[],
    )
    assert version.version >= 1


# ---- 分支规则（人工审批节点）----


def test_approval_branch_rules(db):
    definition = _make_definition(db)
    base_nodes = [
        {"key": "a1", "name": "审批", "node_type": "human_approval"},
        {"key": "t1", "name": "任务", "node_type": "job_match"},
    ]

    def try_edges(edges):
        service.create_draft_version(db, definition.id, nodes=base_nodes, edges=edges)

    with pytest.raises(BizError, match="必须带分支"):
        try_edges([{"source": "a1", "target": "t1"}])
    with pytest.raises(BizError, match="分支仅允许"):
        try_edges([{"source": "a1", "target": "t1", "branch": "maybe"}])
    with pytest.raises(BizError, match="仅人工审批节点"):
        try_edges([{"source": "t1", "target": "a1", "branch": "approve"}])
    with pytest.raises(BizError, match="端点不在节点集"):
        try_edges([{"source": "a1", "target": "ghost"}])
    with pytest.raises(BizError, match="自环"):
        try_edges([{"source": "a1", "target": "a1", "branch": "approve"}])
    with pytest.raises(BizError, match="重复连线"):
        try_edges([{"source": "a1", "target": "t1", "branch": "approve"}] * 2)
    # approve/reject 双分支合法
    version = service.create_draft_version(
        db,
        definition.id,
        nodes=base_nodes,
        edges=[
            {"source": "a1", "target": "t1", "branch": "approve"},
            {"source": "a1", "target": "t1", "branch": "reject"},
        ],
    )
    edge_rows = db.execute(select(WorkflowEdge).where(WorkflowEdge.version_id == version.id)).scalars().all()
    assert {e.branch for e in edge_rows} == {"approve", "reject"}


# ---- 验收①：版本锁定 ----


def test_version_locking_running_run_unaffected(db):
    definition = _make_definition(db)
    v1 = _publish_graph(db, definition.id)
    run = service.start_run(db, definition_id=definition.id, thread_id="th-lock", params={"jd": 1})
    assert run.version_id == v1.id

    # 新版本（不同图）发布 + 废弃 v1：运行实例不受影响
    v2_nodes = [{"key": "only_2", "name": "二号图节点", "node_type": "data_analysis"}]
    v2 = _publish_graph(db, definition.id, nodes=v2_nodes, edges=[])
    service.deprecate_version(db, v1.id)

    db.refresh(run)
    assert run.version_id == v1.id  # 钉死不漂移
    assert db.get(WorkflowDefinitionVersion, run.version_id).status == "deprecated"
    v1_nodes = db.execute(select(WorkflowNode).where(WorkflowNode.version_id == v1.id)).scalars().all()
    v1_edges = db.execute(select(WorkflowEdge).where(WorkflowEdge.version_id == v1.id)).scalars().all()
    assert {n.node_key for n in v1_nodes} == {"parse_1", "approval_1", "match_1", "analysis_1"}
    assert len(v1_edges) == 3  # v1 图数据原样
    v2_rows = db.execute(select(WorkflowNode).where(WorkflowNode.version_id == v2.id)).scalars().all()
    assert {n.node_key for n in v2_rows} == {"only_2"}  # v2 独立图互不影响

    # 已发布/废弃版本拒绝修改（版本锁定服务侧保证）
    for vid in (v1.id, v2.id):
        with pytest.raises(BizError) as excinfo:
            service.update_draft_version(
                db,
                vid,
                nodes=[{"key": "hack", "name": "改图", "node_type": "data_analysis"}],
                edges=[],
            )
        assert excinfo.value.code == 409 and "版本锁定" in excinfo.value.message

    # 草稿/废弃版本不可启动运行；缺省取最新发布
    draft = service.create_draft_version(db, definition.id, nodes=v2_nodes, edges=[])
    with pytest.raises(BizError, match="仅发布版本"):
        service.start_run(db, definition_id=definition.id, thread_id="th-draft", version_id=draft.id)
    run3 = service.start_run(db, definition_id=definition.id, thread_id="th-latest")
    assert run3.version_id == v2.id


# ---- 生命周期 / 运行与步骤登记 ----


def test_publish_rules_and_run_steps(db):
    definition = _make_definition(db)
    with pytest.raises(BizError, match="空图不可发布"):
        service.publish_version(db, service.create_draft_version(db, definition.id, nodes=[], edges=[]).id)

    _publish_graph(db, definition.id)
    run = service.start_run(db, definition_id=definition.id, thread_id="th-steps")
    assert run.status == "running" and run.trigger_type == "manual"

    # 步骤记录：同 attempt 幂等覆盖，重试新 attempt（T-D18 依赖）
    service.record_step(db, run.id, node_key="parse_1", node_type="profile_parse", status="running")
    step = service.record_step(
        db, run.id, node_key="parse_1", node_type="profile_parse",
        status="succeeded", output={"profile_id": 42},
    )
    steps = db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id)).scalars().all()
    assert len(steps) == 1 and steps[0].output_json == {"profile_id": 42}
    assert steps[0].started_at is not None and steps[0].finished_at is not None
    retry = service.record_step(
        db, run.id, node_key="parse_1", node_type="profile_parse",
        status="failed", attempt=2, error="LLM 超时",
    )
    assert retry.id != step.id and retry.attempt == 2

    # 状态机镜像：waiting → 终态落 finished_at
    service.sync_run_status(db, run.id, status="waiting")
    service.sync_run_status(db, run.id, status="succeeded")
    db.refresh(run)
    assert run.status == "succeeded" and run.finished_at is not None
    with pytest.raises(BizError, match="状态非法"):
        service.sync_run_status(db, run.id, status="flying")

    # thread_id 唯一
    with pytest.raises(BizError, match="thread_id 已存在"):
        service.start_run(db, definition_id=definition.id, thread_id="th-steps")


def test_definition_code_rules(db):
    with pytest.raises(BizError, match="node_key"):
        service.create_definition(db, code="Bad Code", name="x")
    service.create_definition(db, code="dup_check", name="一次")
    with pytest.raises(BizError, match="已存在"):
        service.create_definition(db, code="dup_check", name="二次")
    with pytest.raises(BizError) as notfound:
        service.start_run(db, definition_id=99999, thread_id="th-404")
    assert notfound.value.code == 404
