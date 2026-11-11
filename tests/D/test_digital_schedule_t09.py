"""T-D-SELF-09 数字员工定时触发接线用例。

验收对照（docs/modules/D/D模块可自主推进待办清单.md T-D-SELF-09）：
①定时任务与数字员工 collector 的受控接线：开关、执行主体、审计；
②默认关闭验证：未启用时不触发外发/通知（零副作用）；
③幂等与限速：同一周期重复触发不重复落库/通知；
④站内通知闭环：结果入库 + 通知指定管理员，不调用 IM API（无站外外发）。

口径：数字员工定时与工作流定时相互独立（各自开关/间隔/参与清单）；
执行走 digital.service.run_task 的受控系统入口（actor=None 且
triggered_by=None），结果落 digital_tasks + 站内通知，绝不站外外发。

运行：pytest tests/D/test_digital_schedule_t09.py -v（SQLite 内存库，无外部依赖）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.errors import BizError, register_exception_handlers
from app.database import Base, get_db
from app.models import (
    AnalyticsQuerySnapshot,
    DigitalOutboundRequest,
    DigitalTask,
    ParseTask,
    ScreenBatch,
    SysRole,
    SysSetting,
    SysUserRole,
    User,
    WorkflowRun,
)
from app.modules.notifications.models import Notification
from app.modules.workflows import schedule
from app.modules.workflows.router import router as workflows_router

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_HR2 = SimpleNamespace(id=4, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=1)

TABLES = (
    User.__table__,
    # sys_roles/sys_user_roles 建空表：get_role_codes 的 sys_user_roles 查询须成功
    # 返回空才回退 users.role（P3 修复，见 UPD-MASTER-20261110-203044）。
    SysRole.__table__,
    SysUserRole.__table__,
    SysSetting.__table__,
    DigitalTask.__table__,
    DigitalOutboundRequest.__table__,
    Notification.__table__,
    AnalyticsQuerySnapshot.__table__,
    ParseTask.__table__,
    ScreenBatch.__table__,
    WorkflowRun.__table__,
)


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    # 门禁 require_management_guard 内部开 app.database.SessionLocal（真实库）——
    # 重定向到本测试库，角色判定走替身用户（P3 修复）。
    monkeypatch.setattr("app.database.SessionLocal", factory)
    with factory() as db:
        db.add(User(id=2, username="hr1", password_hash="x", role="hr"))
        db.add(User(id=4, username="hr2", password_hash="x", role="hr"))
        db.add(User(id=3, username="p", password_hash="x", role="personal", person_id=1))
        db.commit()

    def fake_get_setting(key: str, default: str | None = None):
        with factory() as db:
            value = db.execute(
                select(SysSetting.setting_value).where(SysSetting.setting_key == key)
            ).scalar_one_or_none()
        return default if value is None else value

    monkeypatch.setattr(schedule, "_get_setting", fake_get_setting)
    yield SimpleNamespace(factory=factory, engine=engine)
    engine.dispose()


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


def _make_client(env, user) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(workflows_router)

    def override_db():
        db = env.factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def client(env):
    return _make_client(env, U_HR)


@pytest.fixture()
def personal_client(env):
    return _make_client(env, U_PERSONAL)


def enable_digital(env, *, task_types: str = "daily_report", admins: str = "2") -> None:
    set_raw_setting(env, schedule.DIGITAL_ENABLED_KEY, "1")
    set_raw_setting(env, schedule.DIGITAL_TASK_TYPES_KEY, task_types)
    set_raw_setting(env, schedule.DIGITAL_INTERVAL_KEY, "60")
    set_raw_setting(env, schedule.DIGITAL_NOTIFY_ADMINS_KEY, admins)


# ---- ①受控接线：开关 / 执行主体 / 审计 ----


def test_digital_default_off_creates_nothing(env):
    """②默认关闭：未启用时扫描零副作用（无任务、无通知、无外发）。"""
    with env.factory() as db:
        db.add(
            AnalyticsQuerySnapshot(
                metric_id="jobs.active", params_json=None,
                result_json={"value": 1}, data_time=datetime.now(), created_by=2,
            )
        )
        db.commit()
    assert loop(env).scan_once() == []
    with env.factory() as db:
        assert db.execute(select(func.count()).select_from(DigitalTask)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(Notification)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(DigitalOutboundRequest)).scalar_one() == 0


def test_digital_enabled_creates_task_and_notifies(env):
    """①③④启用后按周期执行：结果入库 + 站内通知指定管理员；无站外外发。"""
    with env.factory() as db:
        db.add(
            AnalyticsQuerySnapshot(
                metric_id="jobs.active", params_json=None,
                result_json={"value": 3}, data_time=datetime.now(), created_by=2,
            )
        )
        db.commit()
    enable_digital(env, task_types="daily_report", admins="2")

    created = loop(env).scan_once()
    assert len(created) == 1
    with env.factory() as db:
        task = db.get(DigitalTask, created[0])
        assert task.task_type == "daily_report"
        assert task.trigger == "schedule" and task.triggered_by is None
        assert task.status == "success"
        assert task.result_json["snapshot_count"] == 1
        # 审计：站内通知（封闭清单事件）
        notes = db.execute(select(Notification)).scalars().all()
        assert len(notes) == 1
        assert notes[0].event_type == "workflow.needs_human"
        assert notes[0].recipient_user_id == 2
        # ④无站外外发
        assert db.execute(
            select(func.count()).select_from(DigitalOutboundRequest)
        ).scalar_one() == 0


def test_digital_multiple_task_types_each_get_one_task(env):
    """①多场景参与：每个场景各落一行，互不挤占。"""
    enable_digital(env, task_types="daily_report,resume_patrol,todo_reminder")
    created = loop(env).scan_once()
    assert len(created) == 3
    with env.factory() as db:
        types = set(
            db.execute(
                select(DigitalTask.task_type).where(DigitalTask.id.in_(created))
            ).scalars().all()
        )
    assert types == {"daily_report", "resume_patrol", "todo_reminder"}


# ---- ③幂等与限速 ----


def _align_created_at_to_local(env, task_id: int) -> None:
    """把 SQLite 侧 UTC 的 created_at 校正为本地时间（仅测试环境）。

    SQLite 的 `DEFAULT CURRENT_TIMESTAMP` 写 UTC，而 MySQL 为会话本地时间；
    不校正会让 `now - last` 多出时区偏移（东八区 8 小时），限速判定失真。
    """
    with env.factory() as db:
        db.execute(
            DigitalTask.__table__.update()
            .where(DigitalTask.id == task_id)
            .values(created_at=datetime.now())
        )
        db.commit()


def test_digital_interval_prevents_duplicate(env):
    """③限速：间隔内重复扫描不重复落库/通知。"""
    enable_digital(env, task_types="daily_report", admins="2")
    first = loop(env).scan_once()
    assert len(first) == 1
    _align_created_at_to_local(env, first[0])
    second = loop(env).scan_once()
    assert second == []
    with env.factory() as db:
        assert db.execute(select(func.count()).select_from(DigitalTask)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(Notification)).scalar_one() == 1


def test_digital_after_interval_executes_again(env, monkeypatch):
    """③到期后再次执行：间隔与周期窗口都跨过后产生新任务（幂等键随窗口变化）。"""
    enable_digital(env, task_types="daily_report", admins="2")
    set_raw_setting(env, schedule.DIGITAL_INTERVAL_KEY, "5")
    first = loop(env).scan_once()
    assert len(first) == 1

    real_datetime = datetime

    class ShiftedDateTime(real_datetime):
        shift = timedelta(minutes=12)

        @classmethod
        def now(cls, tz=None):
            return real_datetime.now() + cls.shift

    # 把扫描时钟前移 12 分钟：越过 5 分钟限速，也跨过 5 分钟周期窗口
    monkeypatch.setattr(schedule, "datetime", ShiftedDateTime)
    second = loop(env).scan_once()
    assert len(second) == 1 and second[0] != first[0]
    with env.factory() as db:
        assert db.execute(select(func.count()).select_from(DigitalTask)).scalar_one() == 2
        assert db.execute(select(func.count()).select_from(Notification)).scalar_one() == 2


def test_digital_idempotency_key_same_window(env):
    """③幂等：同场景同周期窗口的幂等键稳定，重入只落一行。"""
    enable_digital(env, task_types="daily_report")
    loop(env).scan_once()
    # 绕过到期判定直接重入同窗口键，验证 digital 层幂等
    with env.factory() as db:
        from app.modules.workflows.digital import service as digital_service

        task, created = digital_service.run_task(
            db,
            task_type="daily_report",
            idempotency_key="digital:schedule:daily_report:same-window",
            trigger="schedule",
            actor=None,
            triggered_by=None,
        )
        assert created is True
        task2, created2 = digital_service.run_task(
            db,
            task_type="daily_report",
            idempotency_key="digital:schedule:daily_report:same-window",
            trigger="schedule",
            actor=None,
            triggered_by=None,
        )
        assert created2 is False and task2.id == task.id


def test_digital_independent_of_workflow_switch(env):
    """①独立性：工作流定时关闭时数字员工仍按自身开关执行，反之亦然。"""
    with env.factory() as db:
        db.add(
            AnalyticsQuerySnapshot(
                metric_id="jobs.active", params_json=None,
                result_json={"value": 1}, data_time=datetime.now(), created_by=2,
            )
        )
        db.commit()
    enable_digital(env, task_types="daily_report")
    # 工作流开关未开（默认关闭）
    assert loop(env).scan_once() != []
    with env.factory() as db:
        assert db.execute(select(func.count()).select_from(WorkflowRun)).scalar_one() == 0


# ---- 站内通知闭环（④） ----


def test_digital_no_admins_configured_no_notification(env):
    """④无收件人时只入库不发通知（不报错、不伪造收件人）。"""
    enable_digital(env, task_types="daily_report", admins="")
    created = loop(env).scan_once()
    assert len(created) == 1
    with env.factory() as db:
        assert db.execute(select(func.count()).select_from(Notification)).scalar_one() == 0
        task = db.get(DigitalTask, created[0])
        assert task.status == "success"


# ---- 配置接口：管理侧 / 白名单 / 边界 / 审计 ----


def test_digital_update_settings_requires_management(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=False, interval_minutes=None, definition_codes=None,
                user=U_PERSONAL, digital_enabled=True,
            )
    assert ei.value.code == 403


def test_digital_task_type_whitelist_rejected(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=False, interval_minutes=None, definition_codes=None,
                user=U_HR, digital_task_types=["send_email"],
            )
    assert ei.value.code == 422 and "白名单" in ei.value.message


def test_digital_interval_bounds(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=False, interval_minutes=None, definition_codes=None,
                user=U_HR, digital_interval_minutes=3,
            )
    assert ei.value.code == 422
    with pytest.raises(BizError):
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=False, interval_minutes=None, definition_codes=None,
                user=U_HR, digital_interval_minutes=2000,
            )


def test_digital_notify_admins_validation(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            schedule.update_schedule_settings(
                db, enabled=False, interval_minutes=None, definition_codes=None,
                user=U_HR, digital_notify_admin_ids=[0],
            )
    assert ei.value.code == 422


def test_digital_settings_roundtrip_and_audit(env):
    with env.factory() as db:
        result = schedule.update_schedule_settings(
            db, enabled=False, interval_minutes=None, definition_codes=None,
            user=U_HR, digital_enabled=True, digital_interval_minutes=30,
            digital_task_types=["daily_report", "resume_patrol"],
            digital_notify_admin_ids=[2, 2, 4],
        )
    assert result["digital"] == {
        "enabled": True,
        "interval_minutes": 30,
        "task_types": ["daily_report", "resume_patrol"],
        "notify_admin_ids": [2, 4],
    }
    # 审计：updated_by 落行
    with env.factory() as db:
        row = db.execute(
            select(SysSetting).where(SysSetting.setting_key == schedule.DIGITAL_ENABLED_KEY)
        ).scalar_one()
        assert row.setting_value == "1" and row.updated_by == U_HR.id
    # 关闭
    with env.factory() as db:
        result = schedule.update_schedule_settings(
            db, enabled=False, interval_minutes=None, definition_codes=None,
            user=U_HR2, digital_enabled=False,
        )
    assert result["digital"]["enabled"] is False


# ---- 配置接口 HTTP 层：digital 字段透传与校验 ----


def test_http_put_digital_passthrough(client):
    resp = client.put(
        "/api/workflows/schedule-settings",
        json={
            "enabled": False,
            "digital": {
                "enabled": True,
                "interval_minutes": 15,
                "task_types": ["todo_reminder"],
                "notify_admin_ids": [2, 4],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    digital = resp.json()["data"]["digital"]
    assert digital == {
        "enabled": True,
        "interval_minutes": 15,
        "task_types": ["todo_reminder"],
        "notify_admin_ids": [2, 4],
    }
    # 工作流侧配置未被 digital 请求改动
    assert resp.json()["data"]["enabled"] is False


def test_http_put_digital_whitelist_rejected(client):
    resp = client.put(
        "/api/workflows/schedule-settings",
        json={"enabled": False, "digital": {"task_types": ["send_email"]}},
    )
    assert resp.status_code == 422
    assert "白名单" in resp.text


def test_http_put_digital_admin_ids_type_rejected(client):
    resp = client.put(
        "/api/workflows/schedule-settings",
        json={"enabled": False, "digital": {"notify_admin_ids": [True]}},
    )
    assert resp.status_code == 422


def test_http_get_settings_requires_management(personal_client):
    resp = personal_client.get("/api/workflows/schedule-settings")
    assert resp.status_code == 403
    resp = personal_client.put(
        "/api/workflows/schedule-settings",
        json={"enabled": False, "digital": {"enabled": True}},
    )
    assert resp.status_code == 403
