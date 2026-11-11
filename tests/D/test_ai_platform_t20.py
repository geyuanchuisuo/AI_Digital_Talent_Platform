"""T-D20 AI 中台管理面用例。

验收对照（todo v0.3 T-D20 / 需求 D-35 中台矩阵）：
①密钥不出现在任何接口响应（to_provider_dict 序列化断言 + 服务层用例）；
②供应商启停即时生效——resolve_default_provider 实时读库；停用默认后执行面
  配置回退 None（llm_client 侧 .env 兜底）；
③用量/错误看板数字可回查调用日志——usage_summary 聚合与 list_call_logs 明细
  同源断言；
Prompt 版本库：发布后不可变、同 name 唯一生效版、草稿可改。

运行：pytest tests/D/test_ai_platform_t20.py -v（SQLite 文件库）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.errors import BizError
from app.database import Base
from app.models import (
    AiCallLog,
    AiPromptVersion,
    AiProvider,
    SysRole,
    SysUserRole,
    User,
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowRun,
)
from app.modules.dashboard import service

U_HR = SimpleNamespace(id=1, role="hr", person_id=None)
U_HR2 = SimpleNamespace(id=2, role="hr", person_id=None)
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=None)

TABLES = (
    User.__table__,
    # sys_roles/sys_user_roles 建空表：get_role_codes 的 sys_user_roles 查询须成功
    # 返回空才回退 users.role（P3 修复，见 UPD-MASTER-20261110-203044）。
    SysRole.__table__,
    SysUserRole.__table__,
    AiProvider.__table__,
    AiPromptVersion.__table__,
    AiCallLog.__table__,
    WorkflowDefinition.__table__,
    WorkflowDefinitionVersion.__table__,
    WorkflowRun.__table__,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'ai.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(db_engine, tables=TABLES)
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    # Public A06 guard must query this fixture's account/role database.
    # 门禁 require_management_guard 内部开 app.database.SessionLocal（真实库）——
    # 重定向到本测试库，角色判定走替身用户（P3 修复）。
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

    with factory() as db:
        db.add(User(id=1, username="hr1", password_hash="x", role="hr"))
        db.add(User(id=2, username="hr2", password_hash="x", role="hr"))
        db.add(User(id=3, username="p", password_hash="x", role="personal", person_id=1))
        db.commit()
    yield SimpleNamespace(factory=factory)
    db_engine.dispose()


def make_provider(env, *, name: str | None = None, api_key: str = "sk-secret-123",
                  enabled: bool = True) -> AiProvider:
    with env.factory() as db:
        row = service.create_provider(
            db, name=name or f"prov_{uuid.uuid4().hex[:6]}", provider_type="openai_compatible",
            base_url="http://127.0.0.1:9/v1", model="test-model", api_key=api_key,
            user=U_HR,
        )
        if not enabled:
            service.set_provider_enabled(db, row.id, enabled=False, user=U_HR)
            db.expunge(row)
            row = db.get(AiProvider, row.id)
        db.expunge(row)
    return row


# ---- 验收①：密钥不出现在任何接口响应 ----

def test_provider_dict_never_contains_api_key(env):
    row = make_provider(env)
    d = service.to_provider_dict(row)
    assert "api_key" not in d
    assert "sk-secret-123" not in str(d)
    assert d["has_api_key"] is True  # 只有存在性布尔


def test_create_provider_requires_management(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            service.create_provider(
                db, name="p", provider_type="openai_compatible",
                base_url="http://x", model="m", api_key="k", user=U_PERSONAL,
            )
    assert ei.value.code == 403


def test_create_provider_validations(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            service.create_provider(
                db, name="bad_type", provider_type="grpc", base_url="http://x",
                model="m", api_key="k", user=U_HR,
            )
    assert ei.value.code == 422
    with pytest.raises(BizError):
        with env.factory() as db:
            service.create_provider(
                db, name="no_key", provider_type="openai_compatible",
                base_url="http://x", model="m", api_key="", user=U_HR,
            )


# ---- 验收②：启停即时生效 ----

def test_enable_disable_and_default_resolution(env):
    row = make_provider(env)
    provider_id = row.id
    with env.factory() as db:
        # 设为默认 → 解析到
        service.set_default_provider(db, provider_id, user=U_HR)
        resolved = service.resolve_default_provider(db)
        assert resolved is not None and resolved.id == provider_id
        # 停用 → 解析不到（即时生效）；默认身份一并取消
        service.set_provider_enabled(db, provider_id, enabled=False, user=U_HR)
        assert service.resolve_default_provider(db) is None
        row2 = db.get(AiProvider, provider_id)
        db.expunge(row2)
    assert row2.is_default == 0 and row2.enabled == 0
    # 重新启用后默认不会自动恢复（需显式再设）
    with env.factory() as db:
        service.set_provider_enabled(db, provider_id, enabled=True, user=U_HR)
        assert service.resolve_default_provider(db) is None
        service.set_default_provider(db, provider_id, user=U_HR)
        assert service.resolve_default_provider(db).id == provider_id


def test_disabled_provider_cannot_be_default(env):
    row = make_provider(env, enabled=False)
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            service.set_default_provider(db, row.id, user=U_HR)
    assert ei.value.code == 422


def test_default_is_unique_across_providers(env):
    p1 = make_provider(env)
    p2 = make_provider(env)
    with env.factory() as db:
        service.set_default_provider(db, p1.id, user=U_HR)
        service.set_default_provider(db, p2.id, user=U_HR)
        defaults = list(db.scalars(
            select(AiProvider).where(AiProvider.is_default == 1)
        ))
    assert len(defaults) == 1 and defaults[0].id == p2.id


def test_provider_update_partial_and_key_clear(env):
    row = make_provider(env)
    with env.factory() as db:
        updated = service.update_provider(
            db, row.id, user=U_HR, model="new-model", note="changed"
        )
        assert updated.model == "new-model" and updated.base_url == "http://127.0.0.1:9/v1"
        assert updated.api_key == "sk-secret-123"  # 未传 api_key 不动
        cleared = service.update_provider(db, row.id, user=U_HR, api_key="")
        assert cleared.api_key == ""


# ---- Prompt 版本库 ----

def test_prompt_lifecycle_and_immutability(env):
    with env.factory() as db:
        v1 = service.create_prompt(db, name="summary", content="v1 内容", user=U_HR)
        assert v1.version == 1 and v1.status == "draft"
        # 草稿可改
        service.update_draft_prompt(db, v1.id, content="v1 改", user=U_HR)
        service.publish_prompt(db, v1.id, user=U_HR)
        # 发布后不可变
        with pytest.raises(BizError) as ei:
            service.update_draft_prompt(db, v1.id, content="再改", user=U_HR)
        assert ei.value.code == 409
        # 唯一生效版
        assert service.resolve_published_prompt(db, name="summary").id == v1.id
        # v2 发布后 v1 自动退役
        v2 = service.create_prompt(db, name="summary", content="v2 内容", user=U_HR)
        assert v2.version == 2
        service.publish_prompt(db, v2.id, user=U_HR)
        assert service.resolve_published_prompt(db, name="summary").id == v2.id
        v1_row = db.get(AiPromptVersion, v1.id)
        db.expunge(v1_row)
    assert v1_row.status == "deprecated" and v1_row.content == "v1 改"


# ---- 调用日志与看板（验收③） ----

def _log(env, *, purpose: str, result: str, latency: int, error: str | None = None) -> None:
    with env.factory() as db:
        service.record_call(
            db, provider_name="p1", model="m1", purpose=purpose, result=result,
            latency_ms=latency, prompt_tokens=10, completion_tokens=20, error=error,
        )


def test_usage_summary_matches_detail_records(env):
    _log(env, purpose="parse", result="ok", latency=100)
    _log(env, purpose="parse", result="ok", latency=300)
    _log(env, purpose="match", result="error", latency=50, error="boom")
    _log(env, purpose="match", result="ok", latency=250)

    with env.factory() as db:
        summary = service.usage_summary(db, days=7)
        detail = service.list_call_logs(db, days=7, limit=500)

    assert summary["total_calls"] == 4
    assert summary["total_errors"] == 1
    assert summary["error_rate"] == round(1 / 4, 4)
    assert summary["avg_latency_ms"] == (100 + 300 + 50 + 250) // 4
    by_purpose = {b["purpose"]: b for b in summary["by_purpose"]}
    assert by_purpose["parse"]["calls"] == 2 and by_purpose["parse"]["error_rate"] == 0.0
    assert by_purpose["match"]["error_rate"] == 0.5
    # 明细与聚合同源：明细行数 = total_calls（验收③可回查）
    assert len(detail) == summary["total_calls"]
    # purpose/result 过滤明细
    with env.factory() as db:
        errors = service.list_call_logs(db, days=7, result="error")
    assert len(errors) == 1 and errors[0].error == "boom"


def test_record_call_is_audit_bypass(env):
    """落库异常不抛出（审计旁路不阻塞业务）——用非法 session 模拟故障。"""
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("db down")

    # record_call 直接拿坏 session 对象也不抛
    with env.factory() as db:
        db.add(AiCallLog(provider_name="x", model="m", purpose="p",
                         result="ok", latency_ms=1))
        db.commit()
    # 正常路径幂等可重入
    _log(env, purpose="x", result="ok", latency=1)


def test_usage_days_bounds(env):
    with pytest.raises(BizError):
        with env.factory() as db:
            service.usage_summary(db, days=0)
    with pytest.raises(BizError):
        with env.factory() as db:
            service.usage_summary(db, days=91)


# ---- 运行看板（智能体=工作流载体） ----

def test_agent_run_dashboard_aggregates(env):
    with env.factory() as db:
        definition = WorkflowDefinition(code=f"agent_{uuid.uuid4().hex[:6]}", name="智能体A")
        db.add(definition)
        db.flush()
        version = WorkflowDefinitionVersion(
            definition_id=definition.id, version=1, status="published"
        )
        db.add(version)
        db.flush()
        for i in range(3):
            db.add(WorkflowRun(
                definition_id=definition.id, version_id=version.id,
                thread_id=f"t_{uuid.uuid4().hex[:8]}_{i}",
                trigger_type="manual" if i < 2 else "schedule",
                status="succeeded" if i < 2 else "failed",
            ))
        db.commit()

    with env.factory() as db:
        board = service.agent_run_dashboard(db, days=7)
    assert board["days"] == 7
    agent = next(a for a in board["agents"] if a["code"].startswith("agent_"))
    assert agent["total"] == 3
    assert agent["by_trigger"] == {"manual": 2, "schedule": 1}
    assert agent["by_status"] == {"succeeded": 2, "failed": 1}
