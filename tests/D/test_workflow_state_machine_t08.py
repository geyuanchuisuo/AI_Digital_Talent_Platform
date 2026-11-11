"""D05/D08 工作流状态机边界补测（T-D-SELF-08）。

对照 docs/modules/D/D模块可自主推进待办清单.md T-D-SELF-08 子任务，补齐既有
test_workflow_{models,lifecycle,retry_cancel_t18,schedule_t18}.py 未覆盖的边界：

①版本锁定：废弃版本上的运行实例步骤仍可登记（审计连续），运行镜像不被版本停用改变；
②审核隔离：驳回后重新提交，提交人仍不可自审；无提交人（内部原语边界）管理侧可审；
③回滚：pending_review 目标拒绝；回滚不影响运行中实例且更新审计字段；
④重试：版本废弃后重试仍用钉死版本；引擎对已完结 thread 续跑为空操作；
⑤取消：并发取消（后来者 409）；取消后业务侧迟到回执被拒；取消等待态需管理侧；
⑥重启恢复：多孤儿 running 全部恢复 failed，waiting/succeeded 不碰；
⑦调度：回滚后定时取目标版本；近期失败运行同样阻止新调度；无效执行主体如实失败。

运行：pytest tests/D/test_workflow_state_machine_t08.py -v（SQLite 文件库，无外部依赖）。
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from typing_extensions import TypedDict

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
    SysRole,
    SysSetting,
    SysUserRole,
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
from app.modules.workflows import run_service, schedule, service

U_CREATOR = SimpleNamespace(id=1, role="hr", person_id=None)   # 发起/提交（管理侧）
U_OTHER_HR = SimpleNamespace(id=2, role="hr", person_id=None)  # 审核人（管理侧，非提交人）
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
    # sys_roles/sys_user_roles 建空表：get_role_codes 的 sys_user_roles 查询须成功
    # 返回空才回退 users.role（P3 修复，见 UPD-MASTER-20261110-203044）。
    SysRole.__table__,
    SysUserRole.__table__,
    MetricRegistry.__table__,
    AnalyticsQuerySnapshot.__table__,
    JobProfile.__table__,
    SysSetting.__table__,
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


def _approval(deadline_hours: int | None = 24) -> dict:
    config: dict = {"reason": "复核"}
    if deadline_hours is not None:
        config["deadline_hours"] = deadline_hours
    return {
        "key": "appr", "name": "人工审批", "node_type": "human_approval",
        "position": None, "config": config, "inputs": [], "outputs": [],
    }


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
    # 门禁 require_management_guard 内部开 app.database.SessionLocal（真实库）——
    # 重定向到本测试库，角色判定走替身用户（P3 修复）。
    monkeypatch.setattr("app.database.SessionLocal", factory)
    fake = CacheClient("redis://fake", client=fake_redis.FakeRedis())
    reset_cache(fake)

    run_service.set_session_factory(factory)
    run_service.set_engine(WorkflowEngine(make_saver(ckpt_url), registry_factory=factory))
    yield SimpleNamespace(factory=factory, engine=db_engine, ckpt_url=ckpt_url)
    run_service.set_engine(None)
    run_service.set_session_factory(None)
    reset_cache(None)
    db_engine.dispose()


@pytest.fixture(autouse=True)
def _route_setting_reads(env, monkeypatch):
    """_get_setting 走测试库（生产 get_value 走全局 SessionLocal，与测试库不同源）。"""

    def fake_get_setting(key: str, default: str | None = None):
        with env.factory() as db:
            value = db.execute(
                select(SysSetting.setting_value).where(SysSetting.setting_key == key)
            ).scalar_one_or_none()
        return default if value is None else value

    monkeypatch.setattr(schedule, "_get_setting", fake_get_setting)


def make_published(env, nodes: list[dict], edges: list[dict], *, code: str | None = None):
    """建定义 + 草稿 + 提交 + 审核通过；返回 (definition_id, version_id)。"""
    code = code or f"wf_sm_{uuid.uuid4().hex[:8]}"
    with env.factory() as db:
        definition = service.create_definition(
            db, code=code, name="状态机边界流程", description=None, user=U_CREATOR,
        )
        version = service.create_draft_version(
            db, definition.id, nodes=nodes, edges=edges,
            user=U_CREATOR, created_by=1, note=None,
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_OTHER_HR, approve=True, note=None)
        return definition.id, version.id


def set_raw_setting(env, key: str, value: str) -> None:
    with env.factory() as db:
        row = db.execute(
            select(SysSetting).where(SysSetting.setting_key == key)
        ).scalar_one_or_none()
        if row is None:
            db.add(SysSetting(setting_key=key, setting_value=value, value_type="str"))
        else:
            row.setting_value = value
        db.commit()


def loop(env) -> schedule.WorkflowScheduleLoop:
    return schedule.WorkflowScheduleLoop(session_factory=env.factory, scan_interval=9999)


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


# ---- ①版本锁定：废弃版本上的运行实例不受影响 ----


def test_deprecated_version_run_steps_still_recordable(env):
    """废弃版本上的运行实例：镜像状态不变、步骤仍可登记、版本图数据仍可读。"""
    definition_id, v1_id = make_published(env, [_da("da1")], [])
    with env.factory() as db:
        run = service.start_run(
            db, definition_id=definition_id, thread_id="th-dep-step",
            version_id=v1_id, created_by=1,
        )
        run_id = run.id
    with env.factory() as db:
        service.deprecate_version(db, v1_id)

    with env.factory() as db:
        run = db.get(WorkflowRun, run_id)
        assert run.version_id == v1_id and run.status == "running"
        # 步骤登记不受版本状态影响（审计连续）
        service.record_step(
            db, run_id, node_key="da1", node_type="data_analysis",
            status="succeeded", output={"value": 1},
        )
        detail = service.get_version_detail(db, v1_id)
        assert [n["node_key"] for n in detail["nodes"]] == ["da1"]

    steps = steps_of(env, run_id)
    assert len(steps) == 1 and steps[0].status == "succeeded"


# ---- ②审核隔离 ----


def test_submitter_cannot_approve_after_resubmit(env):
    """驳回后重新提交：submitted_by 刷新为最新提交人，仍不可自审；换人可审。"""
    with env.factory() as db:
        definition = service.create_definition(
            db, code=f"wf_sm_resub_{uuid.uuid4().hex[:8]}", name="重新提交流程",
            description=None, user=U_CREATOR,
        )
        version = service.create_draft_version(
            db, definition.id, nodes=[_da("da1")], edges=[],
            user=U_CREATOR, created_by=1, note=None,
        )
        v1_id = version.id
    with env.factory() as db:
        service.submit_version(db, v1_id, user=U_CREATOR)
    with env.factory() as db:
        service.review_version(db, v1_id, user=U_OTHER_HR, approve=False, note="缺人工审批节点")
    with env.factory() as db:
        service.submit_version(db, v1_id, user=U_CREATOR)

    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            service.review_version(db, v1_id, user=U_CREATOR, approve=True)
    assert ei.value.code == 403 and "四眼" in ei.value.message

    with env.factory() as db:
        v = db.get(WorkflowDefinitionVersion, v1_id)
        assert v.status == "pending_review" and v.submitted_by == U_CREATOR.id
    with env.factory() as db:
        v = service.review_version(db, v1_id, user=U_OTHER_HR, approve=True)
    assert v.status == "published" and v.published_by == U_OTHER_HR.id


def test_review_without_submitter_allows_management(env):
    """内部原语边界：pending_review 且 submitted_by 为空时管理侧可审。

    生产入口 submit_version 必落 submitted_by；None 仅见于内部原语/历史数据，
    此用例锚定当前行为，避免后续被人误改「无提交人一律拒绝」。
    """
    _, v1_id = make_published(env, [_da("da1")], [])
    with env.factory() as db:
        v = db.get(WorkflowDefinitionVersion, v1_id)
        v.status = "pending_review"
        v.submitted_by = None
        db.commit()
    with env.factory() as db:
        v = service.review_version(db, v1_id, user=U_OTHER_HR, approve=True)
    assert v.status == "published"


# ---- ③回滚语义 ----


def test_rollback_pending_review_target_rejected(env):
    definition_id, _ = make_published(env, [_da("da1")], [])
    with env.factory() as db:
        v2 = service.create_draft_version(
            db, definition_id, nodes=[_da("da2")], edges=[], user=U_CREATOR,
        )
        v2_id = v2.id
        service.submit_version(db, v2_id, user=U_CREATOR)  # → pending_review
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            service.rollback_definition(
                db, definition_id, target_version_id=v2_id, user=U_OTHER_HR,
            )
    assert ei.value.code == 422


def test_rollback_does_not_affect_running_run_and_updates_audit(env):
    definition_id, v1_id = make_published(env, [_da("da1")], [])
    with env.factory() as db:
        run = service.start_run(
            db, definition_id=definition_id, thread_id="th-rb-run",
            version_id=v1_id, created_by=1,
        )
        run_id = run.id

    # v2 发布后停用，再回滚到 v2
    with env.factory() as db:
        v2 = service.create_draft_version(
            db, definition_id, nodes=[_da("da2")], edges=[], user=U_CREATOR,
        )
        v2_id = v2.id
        service.submit_version(db, v2_id, user=U_CREATOR)
        service.review_version(db, v2_id, user=U_OTHER_HR, approve=True)
        service.deactivate_published_version(db, v2_id, user=U_OTHER_HR)

    with env.factory() as db:
        target = service.rollback_definition(
            db, definition_id, target_version_id=v2_id, user=U_OTHER_HR,
        )
    assert target.id == v2_id and target.status == "published"
    assert target.published_by == U_OTHER_HR.id and target.published_at is not None

    # 运行中实例钉死 v1，不受回滚影响
    with env.factory() as db:
        run = db.get(WorkflowRun, run_id)
        assert run.version_id == v1_id
    # 新运行缺省取回滚目标 v2
    with env.factory() as db:
        run2 = service.start_run(db, definition_id=definition_id, thread_id="th-rb-new")
        assert run2.version_id == v2_id


# ---- ④重试幂等 ----


def test_retry_after_version_deprecated_uses_pinned_version(env):
    """版本废弃后重试仍用钉死版本：不拒绝、不漂移、失败节点记新 attempt。"""
    definition_id, v1_id = make_published(
        env,
        [_da("da1", "jobs.active"), _da("da_end", "match.threshold_reference")],
        [{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
    )
    # 末节点指标角色收窄到 admin → 首跑 403 失败
    with env.factory() as db:
        row = db.execute(
            select(MetricRegistry).where(
                MetricRegistry.metric_id == "match.threshold_reference"
            )
        ).scalar_one()
        row.allowed_roles = "admin"
        db.commit()

    with env.factory() as db:
        outcome = run_service.start_manual_run(
            db, definition_id=definition_id, params=None, user=U_CREATOR,
        )
        run = outcome["run"]
        assert outcome["result"].status == "failed" and run.status == "failed"
        run_id, thread_id = run.id, run.thread_id

    # 放开角色后废弃 v1，再重试
    with env.factory() as db:
        row = db.execute(
            select(MetricRegistry).where(
                MetricRegistry.metric_id == "match.threshold_reference"
            )
        ).scalar_one()
        row.allowed_roles = "admin,hr"
        db.commit()
    with env.factory() as db:
        service.deprecate_version(db, v1_id)

    with env.factory() as db:
        outcome2 = run_service.retry_failed_run(db, thread_id=thread_id, user=U_CREATOR)
        assert outcome2["run"].version_id == v1_id  # 仍用钉死版本

    run = get_run(env, thread_id)
    assert run.version_id == v1_id
    da_end_steps = [s for s in steps_of(env, run_id) if s.node_key == "da_end"]
    assert [s.attempt for s in da_end_steps] == [1, 2]


def test_engine_retry_on_finished_thread_is_noop(env):
    """引擎对已完结 thread 续跑为空操作：不重执行任何节点（不重复副作用）。"""
    counter: list[int] = []

    class _State(TypedDict, total=False):
        count: int

    def _builder() -> StateGraph:
        def bump(state: _State) -> dict:
            counter.append(1)
            return {"count": (state.get("count") or 0) + 1}

        g = StateGraph(_State)
        g.add_node("bump", bump)
        g.add_edge(START, "bump")
        g.add_edge("bump", END)
        return g

    engine = WorkflowEngine(make_saver(env.ckpt_url))
    first = engine.start("counting", _builder(), {}, thread_id="t-count-noop")
    assert first.status == "succeeded" and counter == [1]

    again = engine.retry("counting", _builder(), "t-count-noop")
    assert again.status == "succeeded"
    assert counter == [1]  # 空操作，未重执行


# ---- ⑤取消边界 ----


def _waiting_run(env, definition_id: int) -> WorkflowRun:
    with env.factory() as db:
        outcome = run_service.start_manual_run(
            db, definition_id=definition_id, params=None, user=U_CREATOR,
        )
        assert outcome["result"].status == "waiting"
        return outcome["run"]


def _approval_definition(env) -> int:
    definition_id, _ = make_published(
        env,
        [_da("da1"), _approval(), _da("da_ok"), _da("da_rej")],
        [
            {"source": "da1", "target": "appr", "branch": "", "condition": None},
            {"source": "appr", "target": "da_ok", "branch": "approve", "condition": None},
            {"source": "appr", "target": "da_rej", "branch": "reject", "condition": None},
        ],
    )
    return definition_id


def test_cancel_waiting_after_already_cancelled_conflicts(env):
    """并发取消：等待态已被其他操作置 cancelled 后，后来者 409。"""
    run = _waiting_run(env, _approval_definition(env))
    with env.factory() as db:
        target = db.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == run.thread_id)
        ).scalar_one()
        target.status = "cancelled"
        db.commit()
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            run_service.cancel_waiting_run(db, thread_id=run.thread_id, user=U_CREATOR)
    assert ei.value.code == 409


def test_cancel_waiting_requires_management(env):
    run = _waiting_run(env, _approval_definition(env))
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            run_service.cancel_waiting_run(db, thread_id=run.thread_id, user=U_PERSONAL)
    assert ei.value.code == 403


def test_sync_run_status_late_receipt_after_cancel_rejected(env):
    """取消后业务侧迟到回执被拒：状态保持 cancelled，不被 failed/succeeded 覆盖。"""
    definition_id, _ = make_published(env, [_da("da1"), _da("da_end")], [])
    with env.factory() as db:
        outcome = run_service.start_manual_run(
            db, definition_id=definition_id, params=None, user=U_CREATOR,
        )
        run = outcome["run"]
        run_id = run.id

    # 造「执行中」快照后取消
    with env.factory() as db:
        target = db.get(WorkflowRun, run_id)
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

    with env.factory() as db:
        returned = service.sync_run_status(db, run_id, status="failed", error="迟到回执")
        assert returned.status == "cancelled"
        again = service.sync_run_status(db, run_id, status="succeeded")
        assert again.status == "cancelled"
        row = db.get(WorkflowRun, run_id)
        assert row.status == "cancelled" and row.finished_at is not None


# ---- ⑥重启恢复 ----


def test_recover_registry_multiple_orphans_waiting_untouched(env):
    """重启恢复：多孤儿 running 全部 failed；waiting/succeeded 一律不碰。"""
    with env.factory() as db:
        db.add(WorkflowEngineRun(graph_name="g1", thread_id="t_orphan_1", status="running"))
        db.add(WorkflowEngineRun(graph_name="g1", thread_id="t_orphan_2", status="running"))
        db.add(WorkflowEngineRun(graph_name="g2", thread_id="t_wait", status="waiting"))
        db.add(WorkflowEngineRun(graph_name="g3", thread_id="t_done", status="succeeded"))
        db.commit()

    stats = recover_registry(env.factory)
    assert stats == {"recovered": 2, "waiting": 1}

    with env.factory() as db:
        statuses = dict(
            db.execute(
                select(WorkflowEngineRun.thread_id, WorkflowEngineRun.status)
            ).all()
        )
    assert statuses == {
        "t_orphan_1": "failed",
        "t_orphan_2": "failed",
        "t_wait": "waiting",
        "t_done": "succeeded",
    }


# ---- ⑦调度边界 ----


def test_schedule_after_rollback_uses_rolled_back_version(env):
    """回滚后定时触发取目标版本（缺省最新发布 = 回滚目标）。"""
    definition_id, v1_id = make_published(env, [_da("da1")], [])
    with env.factory() as db:
        definition = db.get(WorkflowDefinition, definition_id)
        code = definition.code
        v2 = service.create_draft_version(
            db, definition_id, nodes=[_da("da2")], edges=[], user=U_CREATOR,
        )
        v2_id = v2.id
        service.submit_version(db, v2_id, user=U_CREATOR)
        service.review_version(db, v2_id, user=U_OTHER_HR, approve=True)
        service.deactivate_published_version(db, v2_id, user=U_OTHER_HR)
        service.rollback_definition(db, definition_id, target_version_id=v1_id, user=U_OTHER_HR)

    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)
    set_raw_setting(env, schedule.INTERVAL_KEY, "60")
    set_raw_setting(env, schedule.ACTOR_KEY, "1")

    created = loop(env).scan_once()
    assert len(created) == 1
    with env.factory() as db:
        run = db.get(WorkflowRun, created[0])
        db.expunge(run)
    assert run.trigger_type == "schedule" and run.version_id == v1_id


def test_schedule_skips_when_recent_run_failed(env):
    """过期判定只看最近一次 schedule 运行：近期失败同样阻止新调度（不自动补跑）。"""
    definition_id, _ = make_published(env, [_da("da1")], [])
    with env.factory() as db:
        code = db.get(WorkflowDefinition, definition_id).code
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)
    set_raw_setting(env, schedule.INTERVAL_KEY, "1440")

    with env.factory() as db:
        run = service.start_run(
            db, definition_id=definition_id,
            thread_id=f"schedule_{definition_id}_manual_failed",
            trigger_type="schedule", created_by=None,
        )
        run.status = "failed"
        db.commit()

    assert loop(env).scan_once() == []


def test_schedule_invalid_actor_id_fails_run_not_silently_skipped(env):
    """无效执行主体（账号不存在）：运行如实失败留痕，不静默跳过。"""
    definition_id, _ = make_published(env, [_da("da1")], [])
    with env.factory() as db:
        code = db.get(WorkflowDefinition, definition_id).code
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)
    set_raw_setting(env, schedule.ACTOR_KEY, "999")  # 不存在的用户

    created = loop(env).scan_once()
    assert len(created) == 1
    with env.factory() as db:
        run = db.get(WorkflowRun, created[0])
        db.expunge(run)
        engine_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == run.thread_id)
        ).scalar_one_or_none()
        db.expunge(engine_row)
    assert run.created_by == 999
    assert run.status == "failed"  # 引擎如实回执，不静默
    assert engine_row is not None and engine_row.status == "failed"
