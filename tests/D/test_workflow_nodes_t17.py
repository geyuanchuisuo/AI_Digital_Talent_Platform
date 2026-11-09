"""T-D17 五类任务节点 + 人工审批节点用例。

验收对照（todo v0.3 T-D17）：
①data_analysis 节点接真实 analytics registry 查询跑通（jobs.active 统计 SQLite 副本）；
②审批等待不被心跳/恢复重置（复用 T-D04 断言口径：新引擎实例 + recover_registry 不碰 waiting）；
③节点越权调用 403 反例（registry 角色白名单外的发起人 → 节点失败、运行 failed）；
④审批恢复口三道校验（管理侧/四眼/超时 409）。
其余四类业务节点的服务接线以「服务边界 mock」验证映射（真实 LLM 调用待用户配置
Key，DEC-20261107-AGNES-VERIFY 同族；job_match.create_batch 为纯 DB 真实服务，
其完整域内行为由 A 的既有用例覆盖）。
运行：pytest tests/D/test_workflow_nodes_t17.py -v（SQLite 文件库真持久化）。
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import fake_redis
from app.core.cache import CacheClient, reset_cache
from app.core.errors import BizError
from app.core.workflows.engine.registry import recover_registry
from app.core.workflows.engine.runner import WorkflowEngine
from app.core.workflows.engine.saver import make_saver
from app.database import Base
from app.models import (
    AnalyticsQuerySnapshot,
    JobProfile,
    MetricRegistry,
    User,
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowEdge,
    WorkflowEngineRun,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStep,
)
from app.modules.analytics import service as analytics_service
from app.modules.analytics.models import seed_metric_registry
from app.modules.workflows import run_service, service
from app.modules.workflows.nodes.business import (
    assessment_analysis,
    data_analysis,
    job_match,
    profile_parse,
    training_suggestion,
)
from app.modules.workflows.nodes.graph_builder import graph_name_for
from app.modules.workflows.nodes.runtime import NodeContext, resolve_inputs

U_CREATOR = SimpleNamespace(id=1, role="hr", person_id=None)   # 发起运行（管理侧）
U_APPROVER = SimpleNamespace(id=2, role="hr", person_id=None)  # 审批人（管理侧，非发起人）
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=None)

TABLES = (
    WorkflowDefinition.__table__,
    WorkflowDefinitionVersion.__table__,
    WorkflowNode.__table__,
    WorkflowEdge.__table__,
    WorkflowRun.__table__,
    WorkflowRunStep.__table__,
    WorkflowEngineRun.__table__,
    User.__table__,
    MetricRegistry.__table__,
    AnalyticsQuerySnapshot.__table__,
    JobProfile.__table__,
)


def _da(node_key: str, metric_id: str = "jobs.active") -> dict:
    # 落库输入 schema 用 key（get_version_detail 输出才是 node_key）
    return {
        "key": node_key,
        "name": f"数据分析-{node_key}",
        "node_type": "data_analysis",
        "position": None,
        "config": {"metric_id": metric_id},
        "inputs": [],
        "outputs": [],
    }


def _approval(deadline_hours: int | None = 24) -> dict:
    config: dict = {"reason": "高分候选人需要人工复核"}
    if deadline_hours is not None:
        config["deadline_hours"] = deadline_hours
    return {
        "key": "appr",
        "name": "人工审批",
        "node_type": "human_approval",
        "position": None,
        "config": config,
        "inputs": [],
        "outputs": [],
    }


APPROVAL_EDGES = [
    {"source": "da1", "target": "appr", "branch": "", "condition": None},
    {"source": "appr", "target": "da_ok", "branch": "approve", "condition": None},
    {"source": "appr", "target": "da_rej", "branch": "reject", "condition": None},
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'main.db'}", connect_args={"check_same_thread": False}
    )
    ckpt_url = f"sqlite:///{tmp_path / 'ckpt.db'}"
    Base.metadata.create_all(db_engine, tables=TABLES)
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    seed_metric_registry(db_engine)

    with factory() as db:
        db.add(User(id=1, username="creator", password_hash="x", role="hr"))
        db.add(User(id=2, username="approver", password_hash="x", role="hr"))
        db.add(User(id=3, username="personal", password_hash="x", role="personal", person_id=1))
        db.add(
            JobProfile(
                title="高级后端工程师", version=1, is_active=1, jd_text="x",
                requirements_text="x", hard_rule_switches_json={},
                weights_json={}, pass_threshold=Decimal("60.00"),
            )
        )
        db.commit()

    monkeypatch.setattr(analytics_service, "_replica_engine", db_engine)
    monkeypatch.setattr(analytics_service, "SessionLocal", factory)
    fake = CacheClient("redis://fake", client=fake_redis.FakeRedis())
    reset_cache(fake)

    run_service.set_session_factory(factory)
    run_service.set_engine(
        WorkflowEngine(make_saver(ckpt_url), registry_factory=factory)
    )
    yield SimpleNamespace(
        factory=factory, engine=db_engine, ckpt_url=ckpt_url, cache=fake,
    )
    run_service.set_engine(None)
    run_service.set_session_factory(None)
    reset_cache(None)
    db_engine.dispose()


def make_version(env, nodes: list[dict], edges: list[dict]) -> int:
    """走 T-D15 受控路径建版本：hr 发起提交、另一位 hr 审核发布，返回 definition_id。"""
    code = f"flow_{nodes[0]['key']}_{uuid.uuid4().hex[:8]}"
    with env.factory() as db:
        definition = service.create_definition(
            db, code=code, name="测试流程", description=None, user=U_CREATOR,
        )
        version = service.create_draft_version(
            db, definition.id, nodes=nodes, edges=edges, user=U_CREATOR, created_by=1, note=None
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_APPROVER, approve=True, note=None)
        return definition.id


def start_run(env, definition_id: int, params: dict | None = None, user=U_CREATOR):
    with env.factory() as db:
        return run_service.start_manual_run(
            db, definition_id=definition_id, params=params, user=user
        )


def resume(env, thread_id: str, approved: bool, user=U_APPROVER, note: str = ""):
    with env.factory() as db:
        return run_service.resume_approval(
            db, thread_id=thread_id, approved=approved, note=note, user=user
        )


def get_run(env, thread_id: str) -> WorkflowRun:
    with env.factory() as db:
        run = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)
        ).scalar_one()
        db.expunge(run)
    return run


def steps_of(env, run_id: int) -> dict[str, WorkflowRunStep]:
    with env.factory() as db:
        rows = list(
            db.scalars(
                select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id)
            )
        )
        for r in rows:
            db.expunge(r)
    return {r.node_key: r for r in rows}


# ---- ①data_analysis 真实服务端到端 ----

def test_manual_run_data_analysis_succeeds_and_records_steps(env):
    nodes = [_da("da1"), _da("da_end")]
    edges = [{"source": "da1", "target": "da_end", "branch": "", "condition": None}]
    with env.factory() as db:
        definition = service.create_definition(
            db, code="da_single", name="数据分析单节点", description=None, user=U_CREATOR
        )
        version = service.create_draft_version(
            db, definition.id, nodes=nodes, edges=edges, user=U_CREATOR, created_by=1, note=None
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_APPROVER, approve=True, note=None)
        definition_id = definition.id

    outcome = start_run(env, definition_id)
    run, result = outcome["run"], outcome["result"]
    assert result.status == "succeeded"
    assert run.status == "succeeded" and run.finished_at is not None

    steps = steps_of(env, run.id)
    assert steps["da1"].status == "succeeded"
    assert steps["da1"].output_json["value"] == 1  # 种子 1 个活跃岗位
    assert steps["da1"].started_at is not None and steps["da1"].finished_at is not None

    with env.factory() as db:
        detail = run_service.run_detail(db, run.thread_id)
    assert detail["run"]["status"] == "succeeded"
    assert [s["node_key"] for s in detail["steps"]] == ["da1", "da_end"]


# ---- ③节点越权调用 403 反例 ----

def test_data_analysis_role_denied_fails_run_with_403(env):
    with env.factory() as db:
        db.add(MetricRegistry(
            metric_id="admin.only.metric", name="仅管理员", domain="jobs",
            status="active", allowed_roles="admin", cache_ttl=300,
        ))
        db.commit()

    definition_id = make_version(
        env,
        [_da("da1", "admin.only.metric"), _da("da_end")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    outcome = start_run(env, definition_id)
    run, result = outcome["run"], outcome["result"]
    assert result.status == "failed" and run.status == "failed"
    assert "无权访问该资源" in (result.error or "")
    steps = steps_of(env, run.id)
    assert steps["da1"].status == "failed"
    assert "[403]" in (steps["da1"].error or "")  # 越权码随步骤留痕


# ---- ④审批：等待 → 通过/驳回 双分支 ----

def test_approval_wait_then_approve_branch(env):
    definition_id = make_version(
        env, [_da("da1"), _approval(), _da("da_ok"), _da("da_rej")], APPROVAL_EDGES
    )
    outcome = start_run(env, definition_id)
    run, result = outcome["run"], outcome["result"]
    assert result.status == "waiting" and run.status == "waiting"
    payload = result.interrupt
    assert payload["kind"] == "human_approval" and payload["node_key"] == "appr"
    assert payload["deadline"] is not None

    steps = steps_of(env, run.id)
    assert steps["appr"].status == "waiting"
    assert steps["da1"].status == "succeeded"
    assert "da_ok" not in steps and "da_rej" not in steps

    outcome2 = resume(env, run.thread_id, approved=True, note="同意")
    run2, result2 = outcome2["run"], outcome2["result"]
    assert result2.status == "succeeded" and run2.status == "succeeded"
    steps2 = steps_of(env, run.id)
    assert steps2["appr"].status == "succeeded"
    assert steps2["appr"].output_json["decision"] == "approve"
    assert steps2["da_ok"].status == "succeeded"
    assert "da_rej" not in steps2


def test_approval_reject_branch(env):
    definition_id = make_version(
        env, [_da("da1"), _approval(), _da("da_ok"), _da("da_rej")], APPROVAL_EDGES
    )
    outcome = start_run(env, definition_id)
    run = outcome["run"]
    outcome2 = resume(env, run.thread_id, approved=False, note="材料不全")
    assert outcome2["result"].status == "succeeded"
    steps2 = steps_of(env, run.id)
    assert steps2["appr"].output_json["decision"] == "reject"
    assert steps2["da_rej"].status == "succeeded"
    assert "da_ok" not in steps2


# ---- ④恢复口三道校验 ----

def test_approval_requires_management(env):
    definition_id = make_version(
        env, [_da("da1"), _approval(), _da("da_ok"), _da("da_rej")], APPROVAL_EDGES
    )
    run = start_run(env, definition_id)["run"]
    with pytest.raises(BizError) as ei:
        resume(env, run.thread_id, approved=True, user=U_PERSONAL)
    assert ei.value.code == 403


def test_approval_four_eyes_creator_cannot_self_approve(env):
    definition_id = make_version(
        env, [_da("da1"), _approval(), _da("da_ok"), _da("da_rej")], APPROVAL_EDGES
    )
    run = start_run(env, definition_id)["run"]
    with pytest.raises(BizError) as ei:
        resume(env, run.thread_id, approved=True, user=U_CREATOR)
    assert ei.value.code == 403 and "四眼" in ei.value.message


def test_approval_after_deadline_rejected_409(env):
    definition_id = make_version(
        env,
        [_da("da1"), _approval(deadline_hours=-1), _da("da_ok"), _da("da_rej")],
        APPROVAL_EDGES,
    )
    run = start_run(env, definition_id)["run"]
    assert run.status == "waiting"
    with pytest.raises(BizError) as ei:
        resume(env, run.thread_id, approved=True, user=U_APPROVER)
    assert ei.value.code == 409 and "超时" in ei.value.message


# ---- ②等待态不被重启/恢复重置（T-D04 断言口径复用） ----

def test_waiting_survives_engine_restart_and_recovery(env):
    definition_id = make_version(
        env, [_da("da1"), _approval(), _da("da_ok"), _da("da_rej")], APPROVAL_EDGES
    )
    run = start_run(env, definition_id)["run"]
    assert run.status == "waiting"

    # 「杀进程」：全新引擎实例（同检查点文件）；恢复机制绝不触碰 waiting
    run_service.set_engine(
        WorkflowEngine(make_saver(env.ckpt_url), registry_factory=env.factory)
    )
    summary = recover_registry(env.factory)
    assert summary["recovered"] == 0 and summary["waiting"] == 1

    with env.factory() as db:
        for _ in range(3):  # 反复查询不重置
            detail = run_service.run_detail(db, run.thread_id)
            assert detail["run"]["status"] == "waiting"
            assert detail["engine_state"]["next"] == ["appr"]

    outcome = resume(env, run.thread_id, approved=True, user=U_APPROVER)
    assert outcome["result"].status == "succeeded"


def test_two_runs_same_version_do_not_share_compiled_graph(env):
    """回归（图名缓存串图）：引擎按 graph_name 缓存编译图，节点闭包持有发起运行的
    run_id/user_id——同版本第二个运行必须独立编译，步骤记到自己的 run 行。"""
    nodes = [_da("da1"), _da("da_end")]
    edges = [{"source": "da1", "target": "da_end", "branch": "", "condition": None}]
    with env.factory() as db:
        definition = service.create_definition(
            db, code="da_twice", name="同版本两跑", description=None, user=U_CREATOR
        )
        version = service.create_draft_version(
            db, definition.id, nodes=nodes, edges=edges, user=U_CREATOR, created_by=1, note=None
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_APPROVER, approve=True, note=None)
        definition_id = definition.id

    first = start_run(env, definition_id)["run"]
    second = start_run(env, definition_id)["run"]
    assert first.id != second.id
    assert second.status == "succeeded"
    steps2 = steps_of(env, second.id)
    assert set(steps2) == {"da1", "da_end"}  # 串图时这里会是空（步骤落到 first 行）
    assert steps2["da1"].status == "succeeded"
    assert steps_of(env, first.id)["da1"].status == "succeeded"


def test_runtime_resolves_persisted_input_reference_schema():
    node = {"node_key": "match_1", "inputs": [
        {"as": "payload", "from": {"kind": "trigger"}},
        {"as": "profile_id", "from": {"kind": "node_output", "node_key": "parse_1", "field": "profile_id"}},
    ]}
    state = {"trigger": {"job_profile_id": 9}, "vars": {"parse_1": {"profile_id": 5}}}
    assert resolve_inputs(node, state) == {"payload": {"job_profile_id": 9}, "profile_id": 5}


def test_graph_name_fits_engine_registry_column():
    assert len(graph_name_for(123, "x" * 191)) <= 128
    assert graph_name_for(123, "run_a") != graph_name_for(123, "run_b")


# ---- 四类业务节点：服务边界 mock 验证映射（真实调用见文件头说明） ----

def _ctx(env) -> NodeContext:
    return NodeContext(
        run_id=1, thread_id="t-adapter", session_factory=env.factory, user_id=1
    )


def test_job_match_adapter_calls_match_service(env, monkeypatch):
    from app.service import match_service

    captured = {}

    def fake_create_batch(db, job_profile_id, scope, *, user_id):
        captured.update(job=job_profile_id, scope=dict(scope), user=user_id)
        return {"batch_id": 9, "total": 5, "status": "pending", "estimated_llm_calls": 3}

    monkeypatch.setattr(match_service, "create_batch", fake_create_batch)
    node = {"node_key": "m1", "name": "匹配", "node_type": "job_match",
            "config": {"job_profile_id": 7, "scope": {"kind": "all"}}, "inputs": []}
    out = job_match(_ctx(env), node, {})
    assert captured == {"job": 7, "scope": {"kind": "all"}, "user": 1}
    assert out == {"batch_id": 9, "total": 5, "status": "pending", "estimated_llm_calls": 3}


def test_profile_parse_adapter_calls_parse_service(env, monkeypatch):
    from app.service import parse_service

    monkeypatch.setattr(
        parse_service, "extract_structured", lambda text: ("SCHEMA", text)
    )
    monkeypatch.setattr(
        parse_service, "to_dict", lambda schema: {"name": "张三", "raw": schema[1]}
    )
    node = {"node_key": "p1", "name": "解析", "node_type": "profile_parse",
            "config": {}, "inputs": []}
    out = profile_parse(_ctx(env), node, {"text": "张三，5 年后端"})
    assert out == {"name": "张三", "raw": "张三，5 年后端"}


def test_assessment_analysis_adapter_maps_result(env, monkeypatch):
    from app.modules.assessments import service as assessment_service

    fake_detail = {
        "result": SimpleNamespace(
            total_score=Decimal("88.5"),
            dimension_scores_json=[{"name": "沟通", "score": 90}],
            version=2,
        ),
        "published": True,
    }
    seen = {}

    def fake_get_result(db, assignment_id, user):
        seen.update(assignment_id=assignment_id, user_id=getattr(user, "id", None))
        return fake_detail

    monkeypatch.setattr(assessment_service, "get_result", fake_get_result)
    node = {"node_key": "a1", "name": "能力分析", "node_type": "assessment_analysis",
            "config": {"assignment_id": 33}, "inputs": []}
    out = assessment_analysis(_ctx(env), node, {})
    assert seen == {"assignment_id": 33, "user_id": 1}
    assert out["total_score"] == 88.5
    assert out["dimension_scores"] == [{"name": "沟通", "score": 90}]
    assert out["result_version"] == 2 and out["published"] is True


def test_training_suggestion_adapter_builds_draft_request(env, monkeypatch):
    from app.modules.training import service as training_service

    captured = {}

    def fake_create_ai_draft(db, payload, *, admin):
        captured.update(
            employee=payload.employee_user_id,
            assignment=payload.assessment_assignment_id,
            admin_id=getattr(admin, "id", None),
        )
        return {"generated": True, "reason": "", "gap_message": "", "plan": {"id": 5}}

    monkeypatch.setattr(training_service, "create_ai_draft", fake_create_ai_draft)
    node = {
        "node_key": "t1", "name": "培训草稿", "node_type": "training_suggestion",
        "config": {"employee_user_id": 1, "assessment_assignment_id": 33}, "inputs": [],
    }
    out = training_suggestion(_ctx(env), node, {})
    assert captured == {"employee": 1, "assignment": 33, "admin_id": 1}
    assert out["generated"] is True and out["plan"] == {"id": 5}


def test_data_analysis_adapter_requires_metric(env):
    node = {"node_key": "d0", "name": "缺配置", "node_type": "data_analysis",
            "config": {}, "inputs": []}
    with pytest.raises(BizError) as ei:
        data_analysis(_ctx(env), node, {})
    assert ei.value.code == 422 and "metric_id" in ei.value.message
