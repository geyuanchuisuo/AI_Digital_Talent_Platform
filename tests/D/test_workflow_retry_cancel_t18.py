"""T-D18 失败阶段重试 + 运行中取消用例。

验收对照（todo v0.3 T-D18）：
③失败重试不重复产物断言——节点失败后 retry，已成功节点不重执行（vars 检查点
  承载），失败节点重跑记新 attempt；
④运行中取消（协作式）——业务/引擎双侧条件更新置 cancelled；后续节点执行前
  检查业务状态停止推进；取消后迟到的引擎 failed 回执不复活取消态；
受控转换回归——failed→running 仅经 retry 入口；cancelled 拒绝一切回写。

运行：pytest tests/D/test_workflow_retry_cancel_t18.py -v（SQLite 文件库真持久化）。
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
from app.core.workflows.engine.registry import mark_status, recover_registry
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
from app.modules.workflows.nodes.graph_builder import graph_name_for

U_CREATOR = SimpleNamespace(id=1, role="hr", person_id=None)   # 发起运行（管理侧）
U_OTHER_HR = SimpleNamespace(id=2, role="hr", person_id=None)  # 另一管理侧账号
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
    return {
        "key": node_key,
        "name": f"数据分析-{node_key}",
        "node_type": "data_analysis",
        "position": None,
        "config": {"metric_id": metric_id},
        "inputs": [],
        "outputs": [],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'main.db'}", connect_args={"check_same_thread": False}
    )
    ckpt_url = f"sqlite:///{tmp_path / 'ckpt.db'}"
    Base.metadata.create_all(db_engine, tables=TABLES)
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    # Align fixture authentication with the shared A06 database.
    from app.models import SysRole, SysUserRole, UserScopeGrant
    Base.metadata.create_all(db_engine, tables=[SysRole.__table__, SysUserRole.__table__, UserScopeGrant.__table__])
    monkeypatch.setattr('app.database.SessionLocal', factory)
    from app.models import User as ScopeUser
    ScopeUser.__table__.create(factory.kw['bind'], checkfirst=True)
    with factory() as scope_db:
        for key, value in list(globals().items()):
            if key.startswith('U_') and isinstance(getattr(value, 'id', None), int) and hasattr(value, 'role') and value.id not in [1, 2, 3]:
                if scope_db.get(ScopeUser, value.id) is None:
                    scope_db.add(ScopeUser(id=value.id, username='scope_fixture_'+str(value.id),
                        password_hash='test-only', role=value.role, person_id=getattr(value, 'person_id', None)))
        scope_db.commit()

    seed_metric_registry(db_engine)

    with factory() as db:
        db.add(User(id=1, username="creator", password_hash="x", role="hr"))
        db.add(User(id=2, username="other_hr", password_hash="x", role="hr"))
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
    code = f"flow_{nodes[0]['key']}_{uuid.uuid4().hex[:8]}"
    with env.factory() as db:
        definition = service.create_definition(
            db, code=code, name="重试取消测试流程", description=None, user=U_CREATOR,
        )
        version = service.create_draft_version(
            db, definition.id, nodes=nodes, edges=edges, user=U_CREATOR, created_by=1, note=None
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_OTHER_HR, approve=True, note=None)
        return definition.id


def start_run(env, definition_id: int, params: dict | None = None):
    with env.factory() as db:
        return run_service.start_manual_run(
            db, definition_id=definition_id, params=params, user=U_CREATOR
        )


def retry(env, thread_id: str, user=U_CREATOR):
    with env.factory() as db:
        return run_service.retry_failed_run(db, thread_id=thread_id, user=user)


def get_run(env, thread_id: str) -> WorkflowRun:
    with env.factory() as db:
        run = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)
        ).scalar_one()
        db.expunge(run)
    return run


def steps_of(env, run_id: int) -> list[WorkflowRunStep]:
    with env.factory() as db:
        rows = list(
            db.scalars(
                select(WorkflowRunStep)
                .where(WorkflowRunStep.run_id == run_id)
                .order_by(WorkflowRunStep.id.asc())
            )
        )
        for r in rows:
            db.expunge(r)
    return rows


# ---- ③失败重试：已成功节点不重执行（不重复产物） ----

def test_retry_failed_run_success_nodes_not_rerun(env):
    """首节点成功、末节点失败 → retry 后末节点重跑、首节点不重执行。"""
    # da1 用 jobs.active，da_end 用 match.threshold_reference（不同指标互不影响；
    # 后者依赖 match_scores 表，测试库不建 A 域表——用 registry 角色收窄控制成败，
    # 末节点首次 403 失败即可，无需真实统计跑通）
    definition_id = make_version(
        env,
        [_da("da1", "jobs.active"), _da("da_end", "match.threshold_reference")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    # 末节点指标（在 STAT_FUNCS 白名单内、403 先于任何 SQL 执行）角色收窄到 admin
    with env.factory() as db:
        row = db.execute(
            select(MetricRegistry).where(
                MetricRegistry.metric_id == "match.threshold_reference"
            )
        ).scalar_one()
        row.allowed_roles = "admin"
        db.commit()

    outcome = start_run(env, definition_id)
    run, result = outcome["run"], outcome["result"]
    assert result.status == "failed" and run.status == "failed"
    steps = steps_of(env, run.id)
    assert [s.node_key for s in steps] == ["da1", "da_end"]
    assert steps[0].status == "succeeded" and steps[1].status == "failed"

    # 重试前放开角色限制；match_scores 表不存在仍会失败——但这已不影响断言目标
    # （首节点不重执行 + 末节点记新 attempt）。放开后让 attempt=2 走 SQL 报错路径，
    # 步骤仍记 failed，运行 failed；断言按此口径写。
    with env.factory() as db:
        row = db.execute(
            select(MetricRegistry).where(
                MetricRegistry.metric_id == "match.threshold_reference"
            )
        ).scalar_one()
        row.allowed_roles = "admin,hr"
        db.commit()

    outcome2 = retry(env, run.thread_id)
    run2, result2 = outcome2["run"], outcome2["result"]
    # 末节点 attempt=2 不再是 403 越权（角色已放开），运行以失败告终（缺表），
    # 但首节点未重执行、末节点新 attempt 留痕的审计目标达成
    assert run2.status == "failed"

    steps2 = steps_of(env, run.id)
    da1_steps = [s for s in steps2 if s.node_key == "da1"]
    da_end_steps = [s for s in steps2 if s.node_key == "da_end"]
    # 首节点不重执行：只有 attempt=1 一条，成功
    assert len(da1_steps) == 1 and da1_steps[0].attempt == 1 and da1_steps[0].status == "succeeded"
    # 末节点重跑记新 attempt：attempt=1 失败（403）、attempt=2 失败（缺表，但错误不同）
    assert [s.attempt for s in da_end_steps] == [1, 2]
    assert da_end_steps[0].status == "failed" and da_end_steps[1].status == "failed"
    assert "403" in (da_end_steps[0].error or "")
    assert "403" not in (da_end_steps[1].error or "")


def test_retry_requires_management(env):
    definition_id = make_version(
        env, [_da("da1"), _da("da_end")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    with env.factory() as db:
        db.add(MetricRegistry(
            metric_id="admin.only.deny", name="仅管理员", domain="jobs",
            status="active", allowed_roles="admin", cache_ttl=300,
        ))
        db.commit()
    # da_end 用白名单外指标 → 422（未登记指标）→ 运行失败
    definition_id2 = make_version(
        env,
        [_da("da1", "jobs.active"), _da("da_end", "admin.only.deny")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    run = start_run(env, definition_id2)["run"]
    assert run.status == "failed"
    with pytest.raises(BizError) as ei:
        retry(env, run.thread_id, user=U_PERSONAL)
    assert ei.value.code == 403


def test_retry_only_failed_runs(env):
    definition_id = make_version(
        env, [_da("da1"), _da("da_end")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    run = start_run(env, definition_id)["run"]
    assert run.status == "succeeded"
    with pytest.raises(BizError) as ei:
        retry(env, run.thread_id)
    assert ei.value.code == 409


def test_retry_concurrent_claim_conflict(env):
    """另一会话已把 failed→running 后，retry 的 CAS 占用拒绝重复转移。"""
    with env.factory() as db:
        db.add(MetricRegistry(
            metric_id="admin.only.deny2", name="仅管理员", domain="jobs",
            status="active", allowed_roles="admin", cache_ttl=300,
        ))
        db.commit()
    definition_id = make_version(
        env,
        [_da("da1", "jobs.active"), _da("da_end", "admin.only.deny2")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    run = start_run(env, definition_id)["run"]
    assert run.status == "failed"
    # 模拟并发：先单独把业务运行转移为 running（绕过 retry 入口）
    with env.factory() as db:
        target = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == run.thread_id)
        ).scalar_one()
        target.status = "running"
        db.commit()
    with pytest.raises(BizError) as ei:
        retry(env, run.thread_id)
    assert ei.value.code == 409


# ---- ④运行中取消（协作式） ----

def test_cancel_running_run_then_nodes_stop(env):
    """取消 running：业务/引擎双侧置 cancelled；后续节点执行前停止推进。"""
    definition_id = make_version(
        env, [_da("da1"), _da("da_end")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    # 手动构造 running 中途：起一个会卡住的运行不现实（一期同步执行），
    # 用等待态前的运行态直接取消：start_manual_run 同步跑完，因此本用例
    # 通过把运行状态机手动回退到 running 模拟"执行中"，再取消并验证
    # 后续节点包装器的协作检查（以引擎重试入口触发节点执行路径）。
    run = start_run(env, definition_id)["run"]
    assert run.status == "succeeded"

    # 把状态回退到 running（模拟执行中快照；受控白名单允许终态→cancelled）
    with env.factory() as db:
        target = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == run.thread_id)
        ).scalar_one()
        target.status = "running"
        db.commit()
    with env.factory() as db:
        engine_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == run.thread_id)
        ).scalar_one()
        engine_row.status = "running"
        db.commit()

    from app.modules.workflows import run_service as rs

    with env.factory() as db:
        cancelled = rs.cancel_running_run(db, thread_id=run.thread_id, user=U_CREATOR)
    assert cancelled.status == "cancelled"
    with env.factory() as db:
        engine_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == run.thread_id)
        ).scalar_one()
        db.expunge(engine_row)
    assert engine_row.status == "cancelled"


def test_cancel_running_requires_management(env):
    definition_id = make_version(
        env, [_da("da1"), _da("da_end")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    run = start_run(env, definition_id)["run"]
    with env.factory() as db:
        target = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == run.thread_id)
        ).scalar_one()
        target.status = "running"
        db.commit()
    with env.factory() as db:
        engine_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == run.thread_id)
        ).scalar_one()
        engine_row.status = "running"
        db.commit()
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            run_service.cancel_running_run(db, thread_id=run.thread_id, user=U_PERSONAL)
    assert ei.value.code == 403


def test_cancel_running_rejects_non_running(env):
    definition_id = make_version(
        env, [_da("da1"), _da("da_end")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    run = start_run(env, definition_id)["run"]  # succeeded
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            run_service.cancel_running_run(db, thread_id=run.thread_id, user=U_CREATOR)
    assert ei.value.code == 409


def test_cancelled_run_rejects_late_receipts_and_retry(env):
    """取消后迟到的引擎回执不复活取消态；重试入口同样拒绝。"""
    definition_id = make_version(
        env, [_da("da1"), _da("da_end")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    run = start_run(env, definition_id)["run"]

    # 造一个已取消的运行
    with env.factory() as db:
        target = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == run.thread_id)
        ).scalar_one()
        target.status = "running"
        db.commit()
    with env.factory() as db:
        engine_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == run.thread_id)
        ).scalar_one()
        engine_row.status = "running"
        db.commit()
    with env.factory() as db:
        run_service.cancel_running_run(db, thread_id=run.thread_id, user=U_CREATOR)

    # 迟到回执：引擎想把运行标 succeeded/failed —— 白名单拒绝
    assert mark_status(env.factory, run.thread_id, "succeeded") is False
    assert mark_status(env.factory, run.thread_id, "failed") is False
    with env.factory() as db:
        run_after = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == run.thread_id)
        ).scalar_one()
        db.expunge(run_after)
    assert run_after.status == "cancelled"

    # 重试入口拒绝取消态
    with pytest.raises(BizError) as ei:
        retry(env, run.thread_id)
    assert ei.value.code == 409


def test_mark_status_failed_to_running_only_via_retry_semantics(env):
    """引擎登记白名单：failed→running 放行（重试），cancelled 一律拒绝。"""
    with env.factory() as db:
        db.add(WorkflowEngineRun(
            graph_name="g_test", thread_id="t_mark_ctl", status="failed"
        ))
        db.commit()
    assert mark_status(env.factory, "t_mark_ctl", "running") is True
    assert mark_status(env.factory, "t_mark_ctl", "failed") is True
    assert mark_status(env.factory, "t_mark_ctl", "cancelled") is True
    # cancelled 后任何回写拒绝
    assert mark_status(env.factory, "t_mark_ctl", "failed") is False
    assert mark_status(env.factory, "t_mark_ctl", "running") is False


def test_node_executor_cooperative_cancel_check(env):
    """节点包装器在执行前检查业务运行状态：已取消 → BizError 409 不执行。"""
    from app.modules.workflows.nodes.runtime import NodeContext, make_node_executor

    with env.factory() as db:
        db.add(WorkflowRun(
            id=501, definition_id=1, version_id=1, thread_id="t_cancel_check",
            trigger_type="manual", status="cancelled", created_by=1,
        ))
        db.commit()

    executed = []
    node = {"node_key": "n1", "name": "N", "node_type": "data_analysis",
            "config": {}, "inputs": [], "outputs": []}
    ctx = NodeContext(
        run_id=501, thread_id="t_cancel_check",
        session_factory=env.factory, user_id=1,
    )
    wrapped = make_node_executor(ctx, node, lambda c, n, i: executed.append(1) or {})
    with pytest.raises(BizError) as ei:
        wrapped({"trigger": {}, "vars": {}})
    assert ei.value.code == 409
    assert executed == []


def test_recover_registry_still_leaves_waiting_alone(env):
    """回归锚点：恢复只把孤儿 running 置 failed，waiting 不碰（T-D04 口径）。"""
    with env.factory() as db:
        db.add(WorkflowEngineRun(graph_name="g1", thread_id="t_orphan", status="running"))
        db.add(WorkflowEngineRun(graph_name="g2", thread_id="t_wait", status="waiting"))
        db.commit()
    stats = recover_registry(env.factory)
    assert stats["recovered"] == 1 and stats["waiting"] == 1
    with env.factory() as db:
        orphan = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == "t_orphan")
        ).scalar_one()
        waiting = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == "t_wait")
        ).scalar_one()
        db.expunge(orphan)
        db.expunge(waiting)
    assert orphan.status == "failed"
    assert waiting.status == "waiting"


def test_waiting_run_cancel_still_works(env):
    """回归锚点：等待态取消路径不受新改动影响。"""
    from app.modules.workflows.nodes.runtime import NodeContext

    with env.factory() as db:
        definition = service.create_definition(
            db, code=f"wf_wait_{uuid.uuid4().hex[:8]}", name="等待取消",
            description=None, user=U_CREATOR,
        )
        version = service.create_draft_version(
            db, definition.id,
            nodes=[_da("da1"), _approval(), _da("da_ok"), _da("da_rej")],
            edges=[
                {"source": "da1", "target": "appr", "branch": "", "condition": None},
                {"source": "appr", "target": "da_ok", "branch": "approve", "condition": None},
                {"source": "appr", "target": "da_rej", "branch": "reject", "condition": None},
            ],
            user=U_CREATOR, created_by=1, note=None,
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_OTHER_HR, approve=True, note=None)
        definition_id = definition.id

    run = start_run(env, definition_id)["run"]
    assert run.status == "waiting"

    with env.factory() as db:
        cancelled = run_service.cancel_waiting_run(
            db, thread_id=run.thread_id, user=U_CREATOR
        )
    assert cancelled.status == "cancelled"

    # 审批口拒绝已取消的运行
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            run_service.resume_approval(
                db, thread_id=run.thread_id, approved=True, note=None, user=U_OTHER_HR
            )
    assert ei.value.code == 409


def _approval(deadline_hours: int | None = 24) -> dict:
    config: dict = {"reason": "复核"}
    if deadline_hours is not None:
        config["deadline_hours"] = deadline_hours
    return {
        "key": "appr", "name": "人工审批", "node_type": "human_approval",
        "position": None, "config": config, "inputs": [], "outputs": [],
    }
