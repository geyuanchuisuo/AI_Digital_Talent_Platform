"""T-D16 Dify 画布对接用例（导出 / 显式导入 / 对账 / 运行追踪回写）。

验收对照（todo T-D16；执行表 D10 画布对接半边）：
②版本对账一致——导出→导入图恒等 + reconcile identical；
③画布外改动不静默进落库——异类节点/游离连线 422 点名拒绝，显式导入才落新草稿；
运行追踪回写——env 门控默认关、假传输捕获载荷、终态镜像触发、失败不阻塞业务。
真实 Dify 1.19.0 画布可见可改可回传（验收①的画布半边）与回写端点接线归联调
（Docker/WSL 本轮未运行），边界见更新记录。

运行：pytest tests/D/test_dify_bridge_t16.py -v（SQLite 文件库，无外部依赖）。
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import fake_redis
from app.api.deps import get_current_user, get_db
from app.core.cache import CacheClient, reset_cache
from app.core.errors import BizError, register_exception_handlers
from app.database import Base
from app.models import (
    SysRole,
    SysUserRole,
    User,
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStep,
)
from app.modules.workflows import service as wf_service
from app.modules.workflows.dify_bridge import dsl, service as bridge_service, writeback

U_CREATOR = SimpleNamespace(id=1, role="hr", person_id=None)
U_APPROVER = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=None)

TABLES = (
    WorkflowDefinition.__table__,
    WorkflowDefinitionVersion.__table__,
    WorkflowNode.__table__,
    WorkflowEdge.__table__,
    WorkflowRun.__table__,
    WorkflowRunStep.__table__,
    User.__table__,
    # sys_roles/sys_user_roles 建空表：get_role_codes 的 sys_user_roles 查询须成功
    # 返回空才回退 users.role（P3 修复口径，见 UPD-MASTER-20261110-203044）。
    SysRole.__table__,
    SysUserRole.__table__,
)


def _da(key: str, metric: str = "jobs.active") -> dict:
    return {
        "key": key,
        "name": f"数据分析-{key}",
        "node_type": "data_analysis",
        "position": {"x": 120, "y": 80},
        "config": {"metric_id": metric},
        "inputs": [{"as": "threshold", "from": {"kind": "literal", "value": 60}}],
        "outputs": [{"as": "rows", "type": "int"}],
    }


def _appr(key: str = "appr") -> dict:
    return {
        "key": key,
        "name": "人工审批",
        "node_type": "human_approval",
        "position": {"x": 400, "y": 80},
        "config": {"reason": "高分复核", "deadline_hours": 24},
        "inputs": [],
        "outputs": [],
    }


GRAPH_NODES = [_da("da1"), _appr("appr"), _da("da_ok"), _da("da_rej"), _da("da_end")]
GRAPH_EDGES = [
    {"source": "da1", "target": "appr", "branch": "", "condition": None},
    {"source": "appr", "target": "da_ok", "branch": "approve", "condition": None},
    {"source": "appr", "target": "da_rej", "branch": "reject", "condition": None},
    {
        "source": "da_ok",
        "target": "da_end",
        "branch": "",
        "condition": {"from": {"kind": "node_output", "node_key": "da1", "field": "rows"}, "equals": 3},
    },
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'dify.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(db_engine, tables=TABLES)
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id=1, username="creator", password_hash="x", role="hr"))
        db.add(User(id=2, username="approver", password_hash="x", role="hr"))
        db.add(User(id=3, username="personal", password_hash="x", role="personal", person_id=1))
        db.commit()

    # 门禁 require_management_guard 内部开 app.database.SessionLocal（真实库）——
    # 重定向到本测试库，角色判定走替身用户（P3 修复口径）。
    monkeypatch.setattr("app.database.SessionLocal", factory)
    fake = CacheClient("redis://fake", client=fake_redis.FakeRedis())
    reset_cache(fake)
    yield SimpleNamespace(factory=factory, engine=db_engine)
    reset_cache(None)
    db_engine.dispose()


def make_published_definition(env, code: str | None = None) -> int:
    """T-D15 受控生命周期建发布版本：hr 提交、另一 hr 审核发布。"""
    code = code or f"flow_dify_{uuid.uuid4().hex[:8]}"
    with env.factory() as db:
        definition = wf_service.create_definition(
            db, code=code, name="Dify 对接测试流程", description=None, user=U_CREATOR,
        )
        version = wf_service.create_draft_version(
            db, definition.id, nodes=GRAPH_NODES, edges=GRAPH_EDGES,
            user=U_CREATOR, created_by=1, note=None,
        )
        wf_service.submit_version(db, version.id, user=U_CREATOR)
        wf_service.review_version(db, version.id, user=U_APPROVER, approve=True, note=None)
        return definition.id


def get_published(env, definition_id: int) -> WorkflowDefinitionVersion:
    with env.factory() as db:
        return (
            db.execute(
                select(WorkflowDefinitionVersion)
                .where(
                    WorkflowDefinitionVersion.definition_id == definition_id,
                    WorkflowDefinitionVersion.status == "published",
                )
            )
            .scalar_one()
        )


def export_yaml(env, definition_id: int) -> str:
    version = get_published(env, definition_id)
    with env.factory() as db:
        return bridge_service.export_dsl(db, version.id, user=U_CREATOR)["yaml"]


def normalized(detail: dict) -> dict:
    return dsl.normalize_graph(detail["nodes"], detail["edges"])


# ---- 导出形状 ----

def test_export_envelope_and_mapping(env):
    definition_id = make_published_definition(env)
    text = export_yaml(env, definition_id)
    data = yaml.safe_load(text)
    assert data["kind"] == "app"
    assert data["app"]["mode"] == "workflow"
    assert data["version"] == dsl.DIFY_DSL_VERSION
    graph = data["workflow"]["graph"]
    assert {n["id"] for n in graph["nodes"]} == {"da1", "appr", "da_ok", "da_rej", "da_end"}
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["da1"]["data"]["type"] == "ait_data_analysis"
    assert by_id["appr"]["data"]["type"] == "ait_human_approval"
    assert by_id["da1"]["data"]["ait_config"] == {"metric_id": "jobs.active"}
    assert by_id["da1"]["data"]["ait_inputs"][0]["as"] == "threshold"
    assert by_id["da1"]["position"] == {"x": 120, "y": 80}
    edges = {(e["source"], e["target"]): e for e in graph["edges"]}
    assert edges[("appr", "da_ok")]["sourceHandle"] == "approve"
    assert edges[("appr", "da_rej")]["sourceHandle"] == "reject"
    assert edges[("da1", "appr")]["sourceHandle"] == "source"
    assert edges[("da_ok", "da_end")]["data"]["ait_condition"]["equals"] == 3


# ---- 验收②：往返对账一致 ----

def test_roundtrip_import_recreates_identical_graph(env):
    definition_id = make_published_definition(env)
    text = export_yaml(env, definition_id)
    published = get_published(env, definition_id)
    with env.factory() as db:
        result = bridge_service.import_dsl(db, definition_id, text, user=U_APPROVER)
    assert result["status"] == "draft"
    assert result["node_count"] == 5 and result["edge_count"] == 4
    with env.factory() as db:
        v1 = wf_service.get_version_detail(db, published.id)
        v2 = wf_service.get_version_detail(db, result["version_id"])
    assert normalized(v1) == normalized(v2), "导出→导入后的图必须与原版本恒等（验收②）"
    with env.factory() as db:
        report = bridge_service.reconcile_dsl(db, result["version_id"], text, user=U_CREATOR)
    assert report["identical"] is True
    assert report["nodes"] == {"added": [], "removed": [], "changed": []}


def test_import_creates_new_draft_never_touches_published(env):
    definition_id = make_published_definition(env)
    before = get_published(env, definition_id)
    text = export_yaml(env, definition_id)
    with env.factory() as db:
        result = bridge_service.import_dsl(db, definition_id, text, user=U_CREATOR)
    with env.factory() as db:
        after = db.get(WorkflowDefinitionVersion, before.id)
        nodes = (
            db.execute(select(WorkflowNode).where(WorkflowNode.version_id == before.id)).scalars().all()
        )
        assert after.status == "published"
        assert after.version == before.version
        assert len(nodes) == 5, "版本锁定：已发布版本的节点行不得被导入动作触碰"
        draft = db.get(WorkflowDefinitionVersion, result["version_id"])
        assert draft.status == "draft" and draft.version == before.version + 1


# ---- 验收③：画布外改动不静默进落库 ----

def test_import_rejects_foreign_canvas_nodes(env):
    definition_id = make_published_definition(env)
    data = yaml.safe_load(export_yaml(env, definition_id))
    data["workflow"]["graph"]["nodes"].append(
        {"id": "llm_1", "type": "custom", "position": {"x": 0, "y": 0},
         "data": {"type": "llm", "title": "画布新增节点"}}
    )
    with env.factory() as db:
        with pytest.raises(BizError) as ei:
            bridge_service.import_dsl(db, definition_id, yaml.safe_dump(data, allow_unicode=True), user=U_CREATOR)
    assert "llm_1" in str(ei.value) and "不静默丢弃" in str(ei.value)


def test_import_rejects_edges_to_foreign_nodes(env):
    definition_id = make_published_definition(env)
    data = yaml.safe_load(export_yaml(env, definition_id))
    data["workflow"]["graph"]["edges"].append(
        {"id": "edge-x", "source": "da1", "target": "llm_1", "sourceHandle": "source",
         "targetHandle": "target", "type": "custom", "zIndex": 0, "data": {}}
    )
    with env.factory() as db:
        with pytest.raises(BizError) as ei:
            bridge_service.import_dsl(db, definition_id, yaml.safe_dump(data, allow_unicode=True), user=U_CREATOR)
    assert "不静默丢弃" in str(ei.value)


def test_import_rejects_unknown_ait_type(env):
    definition_id = make_published_definition(env)
    data = yaml.safe_load(export_yaml(env, definition_id))
    data["workflow"]["graph"]["nodes"][0]["data"]["type"] = "ait_时空穿越"
    with env.factory() as db:
        with pytest.raises(BizError) as ei:
            bridge_service.import_dsl(db, definition_id, yaml.safe_dump(data, allow_unicode=True), user=U_CREATOR)
    assert "白名单" in str(ei.value)


def test_import_rejects_bad_envelope(env):
    definition_id = make_published_definition(env)
    cases = [
        "not: [a, yaml, app]",                                   # 无 app 段
        yaml.safe_dump({"kind": "app", "app": {"mode": "chat"}}),  # mode 非 workflow
        yaml.safe_dump({"kind": "app", "app": {"mode": "workflow"},
                        "version": "9.9.9", "workflow": {"graph": {"nodes": [], "edges": []}}}),  # 版本过高
        "version: [\n  broken",                                   # YAML 解析失败
    ]
    for text in cases:
        with env.factory() as db:
            with pytest.raises(BizError):
                bridge_service.import_dsl(db, definition_id, text, user=U_CREATOR)


def test_import_rejects_empty_graph(env):
    definition_id = make_published_definition(env)
    data = yaml.safe_load(export_yaml(env, definition_id))
    data["workflow"]["graph"] = {"nodes": [], "edges": []}
    with env.factory() as db:
        with pytest.raises(BizError) as ei:
            bridge_service.import_dsl(db, definition_id, yaml.safe_dump(data, allow_unicode=True), user=U_CREATOR)
    assert "图为空" in str(ei.value)


# ---- 对账报告 ----

def test_reconcile_reports_drift(env):
    definition_id = make_published_definition(env)
    version = get_published(env, definition_id)
    data = yaml.safe_load(export_yaml(env, definition_id))
    # 画布外改动：改标题 + 删一节点一连线 + 增一节点
    data["workflow"]["graph"]["nodes"][0]["data"]["title"] = "改名了"
    graph = data["workflow"]["graph"]
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != "da_rej"]
    graph["edges"] = [e for e in graph["edges"] if e["target"] != "da_rej"]
    graph["nodes"].append(
        {"id": "da_new", "type": "custom", "position": {"x": 1, "y": 2},
         "data": {"type": "ait_data_analysis", "title": "新节点", "ait_config": None,
                  "ait_inputs": [], "ait_outputs": []}}
    )
    with env.factory() as db:
        report = bridge_service.reconcile_dsl(db, version.id, yaml.safe_dump(data, allow_unicode=True), user=U_CREATOR)
    assert report["identical"] is False
    assert report["nodes"]["added"] == ["da_new"]
    assert report["nodes"]["removed"] == ["da_rej"]
    changed = {c["node_key"]: c["fields"] for c in report["nodes"]["changed"]}
    assert changed["da1"] == ["name"]
    with env.factory() as db:
        versions = (
            db.execute(
                select(WorkflowDefinitionVersion).where(
                    WorkflowDefinitionVersion.definition_id == definition_id
                )
            ).scalars().all()
        )
        assert len(versions) == 1, "对账是只读报告，不得建版本"


# ---- HTTP 门禁（管理侧） ----

@pytest.fixture()
def client(env, monkeypatch):
    from app.modules.workflows.router import router as workflows_router

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(workflows_router)
    current = {"user": U_CREATOR}

    def override_db():
        db = env.factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    yield SimpleNamespace(client=TestClient(app), current=current)
    app.dependency_overrides.clear()


def test_endpoints_management_gated(env, client):
    definition_id = make_published_definition(env)
    version = get_published(env, definition_id)
    text = export_yaml(env, definition_id)
    client.current["user"] = U_PERSONAL
    resp = client.client.get(f"/api/workflows/versions/{version.id}/dify-dsl")
    assert resp.status_code == 403
    resp = client.client.post(
        f"/api/workflows/definitions/{definition_id}/dify-import", json={"yaml": text}
    )
    assert resp.status_code == 403
    resp = client.client.post(
        f"/api/workflows/versions/{version.id}/dify-reconcile", json={"yaml": text}
    )
    assert resp.status_code == 403
    client.current["user"] = U_CREATOR
    resp = client.client.get(f"/api/workflows/versions/{version.id}/dify-dsl")
    assert resp.status_code == 200
    assert resp.json()["data"]["node_count"] == 5
    resp = client.client.post(
        f"/api/workflows/versions/{version.id}/dify-reconcile", json={"yaml": text}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["identical"] is True


# ---- 运行追踪回写 ----

def test_writeback_payload_shape(env):
    definition_id = make_published_definition(env)
    version = get_published(env, definition_id)
    with env.factory() as db:
        run = WorkflowRun(
            definition_id=definition_id, version_id=version.id, thread_id="th-dify-1",
            trigger_type="manual", status="succeeded", finished_at=None,
        )
        db.add(run)
        db.flush()
        db.add(WorkflowRunStep(run_id=run.id, node_key="da1", node_type="data_analysis",
                               attempt=1, status="succeeded", error=None))
        db.add(WorkflowRunStep(run_id=run.id, node_key="appr", node_type="human_approval",
                               attempt=1, status="waiting", error="超时" * 200))
        db.commit()
        payload = writeback.build_run_trace_payload(
            run,
            db.execute(
                select(WorkflowRunStep)
                .where(WorkflowRunStep.run_id == run.id)
                .order_by(WorkflowRunStep.id.asc())
            ).scalars().all(),
            definition_code="flow_x", version_number=version.version,
        )
    assert payload["schema"] == "ait.dify.run-trace.v1"
    assert payload["thread_id"] == "th-dify-1"
    assert payload["status"] == "succeeded"
    assert len(payload["steps"]) == 2
    assert payload["steps"][1]["node_key"] == "appr"
    assert payload["steps"][1]["error"] is not None and len(payload["steps"][1]["error"]) <= 300
    assert payload["steps"][0]["error"] is None


def test_writeback_skips_without_url(env, monkeypatch):
    monkeypatch.delenv("DIFY_RUN_TRACE_URL", raising=False)
    assert writeback.push_run_trace({"schema": "x"}) == {"skipped": "DIFY_RUN_TRACE_URL not configured"}


def test_writeback_fake_transport_and_failure(env, monkeypatch):
    monkeypatch.setenv("DIFY_RUN_TRACE_URL", "http://127.0.0.1:9/dify-hook")
    captured: list[tuple[str, dict]] = []

    def ok_transport(url, payload):
        captured.append((url, payload))
        return 200

    result = writeback.push_run_trace({"schema": "x"}, transport=ok_transport)
    assert result == {"sent": True, "status": 200}
    assert captured[0][0] == "http://127.0.0.1:9/dify-hook"

    def bad_transport(url, payload):
        raise RuntimeError("连接被拒")

    result = writeback.push_run_trace({"schema": "x"}, transport=bad_transport)
    assert result["sent"] is False and "连接被拒" in result["error"]


def test_terminal_mirror_triggers_writeback(env, monkeypatch):
    definition_id = make_published_definition(env)
    version = get_published(env, definition_id)
    monkeypatch.setenv("DIFY_RUN_TRACE_URL", "http://127.0.0.1:9/dify-hook")
    captured: list[dict] = []
    monkeypatch.setattr(
        writeback, "_default_transport",
        lambda url, payload: (captured.append(payload), 200)[1],
    )
    with env.factory() as db:
        run = WorkflowRun(
            definition_id=definition_id, version_id=version.id, thread_id="th-dify-2",
            trigger_type="manual", status="running",
        )
        db.add(run)
        db.commit()
        run_id = run.id
    with env.factory() as db:
        wf_service.sync_run_status(db, run_id, status="waiting")  # 非终态：不回写
    assert captured == []
    with env.factory() as db:
        wf_service.sync_run_status(db, run_id, status="succeeded")  # 终态：回写
    assert len(captured) == 1
    assert captured[0]["thread_id"] == "th-dify-2"
    assert captured[0]["status"] == "succeeded"
    assert captured[0]["definition"]["code"].startswith("flow_dify_")
    # succeeded→cancelled 同为进入终态的合法转移（迟到回执白名单），挂点照常回写
    with env.factory() as db:
        wf_service.sync_run_status(db, run_id, status="cancelled")
    assert len(captured) == 2
    assert captured[1]["status"] == "cancelled"
