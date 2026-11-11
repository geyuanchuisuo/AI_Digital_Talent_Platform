"""T-D18 定时触发用例（验收④：默认关闭 + 管理员启用 + 审计）。

口径：
- 默认关闭：sys_settings 缺行时 scan_once 不创建任何运行；
- 启用后到期创建：trigger_type=schedule、钉发布版本、间隔内不重复；
- 无执行主体（actor 缺省）只登记不执行引擎——运行留 running 如实可见；
- 配置变更经管理侧（个人 403），interval 边界校验。

运行：pytest tests/D/test_workflow_schedule_t18.py -v（SQLite 文件库）。
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

U_CREATOR = SimpleNamespace(id=1, role="hr", person_id=None)
U_OTHER_HR = SimpleNamespace(id=2, role="hr", person_id=None)
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


def make_published_definition(env, code: str) -> int:
    with env.factory() as db:
        definition = service.create_definition(
            db, code=code, name="定时流程", description=None, user=U_CREATOR,
        )
        version = service.create_draft_version(
            db, definition.id,
            nodes=[_da("da1"), _da("da_end")],
            edges=[{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
            user=U_CREATOR, created_by=1, note=None,
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_OTHER_HR, approve=True, note=None)
        return definition.id


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


def loop(env) -> schedule.WorkflowScheduleLoop:
    return schedule.WorkflowScheduleLoop(session_factory=env.factory, scan_interval=9999)


# ---- 验收④：默认关闭 ----

def test_default_off_creates_nothing(env):
    definition_id = make_published_definition(env, f"wf_sched_off_{uuid.uuid4().hex[:6]}")
    created = loop(env).scan_once()
    assert created == []
    with env.factory() as db:
        runs = list(db.scalars(select(WorkflowRun)))
    assert runs == []


def test_enabled_without_definitions_creates_nothing(env):
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    created = loop(env).scan_once()
    assert created == []


# ---- 启用后：到期创建 schedule 运行 ----

def test_enabled_creates_schedule_run_and_pinned_version(env):
    definition_id = make_published_definition(env, f"wf_sched_a_{uuid.uuid4().hex[:6]}")
    code = f"wf_sched_a_{uuid.uuid4().hex[:6]}"  # 与上面不同 code；用真实 code
    with env.factory() as db:
        definition = db.get(WorkflowDefinition, definition_id)
        code = definition.code
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)
    set_raw_setting(env, schedule.INTERVAL_KEY, "5")

    created = loop(env).scan_once()
    assert len(created) == 1
    with env.factory() as db:
        run = db.get(WorkflowRun, created[0])
        db.expunge(run)
    assert run.trigger_type == "schedule"
    assert run.definition_id == definition_id
    # 钉发布版本
    with env.factory() as db:
        version = db.get(WorkflowDefinitionVersion, run.version_id)
        db.expunge(version)
    assert version.status == "published"


def test_interval_prevents_duplicate_run(env):
    definition_id = make_published_definition(env, f"wf_sched_b_{uuid.uuid4().hex[:6]}")
    with env.factory() as db:
        code = db.get(WorkflowDefinition, definition_id).code
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)
    set_raw_setting(env, schedule.INTERVAL_KEY, "1440")

    first = loop(env).scan_once()
    assert len(first) == 1
    # 间隔未到：不再创建
    second = loop(env).scan_once()
    assert second == []
    with env.factory() as db:
        count = len(list(db.scalars(
            select(WorkflowRun).where(
                WorkflowRun.definition_id == definition_id,
                WorkflowRun.trigger_type == "schedule",
            )
        )))
    assert count == 1


def test_unknown_definition_code_skipped(env):
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, "no_such_code")
    created = loop(env).scan_once()
    assert created == []


def test_definition_without_published_version_skipped(env):
    with env.factory() as db:
        definition = service.create_definition(
            db, code=f"wf_sched_draft_{uuid.uuid4().hex[:6]}", name="草稿",
            description=None, user=U_CREATOR,
        )
        code = definition.code
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)
    created = loop(env).scan_once()
    assert created == []


# ---- 执行主体：未配置只登记不执行（如实可见） ----

def test_no_actor_registers_without_engine_execution(env):
    definition_id = make_published_definition(env, f"wf_sched_c_{uuid.uuid4().hex[:6]}")
    with env.factory() as db:
        code = db.get(WorkflowDefinition, definition_id).code
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)

    created = loop(env).scan_once()
    assert len(created) == 1
    with env.factory() as db:
        run = db.get(WorkflowRun, created[0])
        db.expunge(run)
        engine_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == run.thread_id)
        ).scalar_one_or_none()
    # 未配置执行主体：运行登记但引擎不启动（无 WorkflowEngineRun 行）
    assert run.created_by is None
    assert run.status == "running"
    assert engine_row is None


def test_with_actor_executes_engine(env):
    definition_id = make_published_definition(env, f"wf_sched_d_{uuid.uuid4().hex[:6]}")
    with env.factory() as db:
        code = db.get(WorkflowDefinition, definition_id).code
    set_raw_setting(env, schedule.ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DEFS_KEY, code)
    set_raw_setting(env, schedule.ACTOR_KEY, "1")  # creator（管理侧，jobs.active 允许 hr）

    created = loop(env).scan_once()
    assert len(created) == 1
    with env.factory() as db:
        run = db.get(WorkflowRun, created[0])
        db.expunge(run)
        engine_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == run.thread_id)
        ).scalar_one_or_none()
        db.expunge(engine_row)
    assert run.created_by == 1
    assert run.status == "succeeded"
    assert engine_row is not None and engine_row.status == "succeeded"


# ---- 管理侧配置接口（审计：updated_by 落 sys_settings） ----

def test_update_settings_requires_management(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=True, interval_minutes=None,
                definition_codes=None, user=U_PERSONAL,
            )
    assert ei.value.code == 403


def test_update_settings_interval_bounds(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=True, interval_minutes=3,
                definition_codes=None, user=U_CREATOR,
            )
    assert ei.value.code == 422
    with pytest.raises(BizError):
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=True, interval_minutes=2000,
                definition_codes=None, user=U_CREATOR,
            )


def test_update_settings_roundtrip_and_audit(env):
    definition_id = make_published_definition(env, f"wf_sched_e_{uuid.uuid4().hex[:6]}")
    with env.factory() as db:
        code = db.get(WorkflowDefinition, definition_id).code
    with env.factory() as db:
        result = schedule.update_schedule_settings(
            db, enabled=True, interval_minutes=30,
            definition_codes=[code, code, " other "], user=U_CREATOR,
            actor_user_id=1,
        )
    assert result == {
        "enabled": True,
        "interval_minutes": 30,
        "definition_codes": [code, "other"],
        "actor_user_id": 1,
        # 数字员工定时默认关闭（独立开关，未配置时零副作用）
        "digital": {
            "enabled": False,
            "interval_minutes": schedule.DIGITAL_DEFAULT_INTERVAL_MINUTES,
            "task_types": [],
            "notify_admin_ids": [],
        },
    }
    # 审计：updated_by 落行
    with env.factory() as db:
        row = db.execute(
            select(SysSetting).where(SysSetting.setting_key == schedule.ENABLED_KEY)
        ).scalar_one()
        db.expunge(row)
    assert row.setting_value == "1" and row.updated_by == 1
    # 关闭
    with env.factory() as db:
        result = schedule.update_schedule_settings(
            db, enabled=False, interval_minutes=None,
            definition_codes=None, user=U_OTHER_HR,
        )
    assert result["enabled"] is False
