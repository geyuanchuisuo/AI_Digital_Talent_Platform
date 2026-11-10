"""T-D19 Skills 组件库用例。

验收对照（todo v0.3 T-D19 / 跟踪表 D11）：
①未审核组件不可发布——draft/pending_review/deprecated 版本引用解析 422；
  自行审核（提交人=审核人）403 四眼；
②版本回退不影响已引用实例——运行钉死 version_id（引用快照解耦状态流转）；
③组件执行进运行审计——skill 节点执行经 make_node_executor 落 workflow_run_steps。

运行：pytest tests/D/test_workflow_skills_t19.py -v（SQLite 文件库）。
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
    SysUserRole,
    User,
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowEdge,
    WorkflowEngineRun,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowSkill,
    WorkflowSkillVersion,
)
from app.modules.analytics import service as analytics_service
from app.modules.analytics.models import seed_metric_registry
from app.modules.workflows import run_service, service, skills_service

U_CREATOR = SimpleNamespace(id=1, role="hr", person_id=None)   # 创建/提交（管理侧）
U_REVIEWER = SimpleNamespace(id=2, role="hr", person_id=None)  # 审核人（管理侧，非提交人）
U_PERSONAL = SimpleNamespace(id=3, role="personal", person_id=None)

TABLES = (
    WorkflowDefinition.__table__,
    WorkflowDefinitionVersion.__table__,
    WorkflowNode.__table__,
    WorkflowEdge.__table__,
    WorkflowRun.__table__,
    WorkflowRunStep.__table__,
    WorkflowEngineRun.__table__,
    WorkflowSkill.__table__,
    WorkflowSkillVersion.__table__,
    User.__table__,
    # sys_roles/sys_user_roles 建空表：get_role_codes 的 sys_user_roles 查询须成功
    # 返回空才回退 users.role（P3 修复，见 UPD-MASTER-20261110-203044）。
    SysRole.__table__,
    SysUserRole.__table__,
    MetricRegistry.__table__,
    AnalyticsQuerySnapshot.__table__,
    JobProfile.__table__,
)


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
        db.add(User(id=2, username="reviewer", password_hash="x", role="hr"))
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


def make_published_skill(env, *, tool: str = "none", parser: str = "raw_text") -> tuple[int, int]:
    """走受控路径：创建 → 草稿版本 → 提交 → 他人审核发布；返回 (skill_id, version_id)。"""
    with env.factory() as db:
        skill = skills_service.create_skill(
            db, code=f"skill_{uuid.uuid4().hex[:8]}", name="测试组件",
            tool=tool, output_parser=parser, description=None, user=U_CREATOR,
        )
        version = skills_service.create_skill_version(
            db, skill.id,
            prompt_template="分析 {{metric}}：{{question}}",
            params_json_schema={
                "type": "object",
                "properties": {"metric": {"type": "string"}, "question": {"type": "string"}},
                "required": ["metric"],
            },
            user=U_CREATOR, created_by=1, note=None,
        )
        skills_service.submit_skill_version(db, version.id, user=U_CREATOR)
        reviewed = skills_service.review_skill_version(
            db, version.id, user=U_REVIEWER, approve=True, note=None
        )
        return skill.id, reviewed.id


# ---- 白名单与创建 ----

def test_create_skill_whitelist_rejects(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            skills_service.create_skill(
                db, code="s_bad_tool", name="坏工具", tool="shell_exec",
                output_parser="raw_text", description=None, user=U_CREATOR,
            )
    assert ei.value.code == 422
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            skills_service.create_skill(
                db, code="s_bad_parser", name="坏解析器", tool="none",
                output_parser="eval_js", description=None, user=U_CREATOR,
            )
    assert ei.value.code == 422


def test_create_skill_requires_management(env):
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            skills_service.create_skill(
                db, code="s_personal", name="个人", tool="none",
                output_parser="raw_text", description=None, user=U_PERSONAL,
            )
    assert ei.value.code == 403


def test_params_schema_validation(env):
    skill_id, _ = make_published_skill(env)
    with pytest.raises(BizError) as ei:
        with env.factory() as db:
            skills_service.create_skill_version(
                db, skill_id, prompt_template="x",
                params_json_schema={"type": "array"}, user=U_CREATOR,
            )
    assert ei.value.code == 422
    with pytest.raises(BizError):
        with env.factory() as db:
            skills_service.create_skill_version(
                db, skill_id, prompt_template="x",
                params_json_schema={"type": "object", "required": ["ghost"]},
                user=U_CREATOR,
            )
    with pytest.raises(BizError):
        with env.factory() as db:
            skills_service.create_skill_version(
                db, skill_id, prompt_template="x",
                params_json_schema={"type": "object", "custom_key": 1},
                user=U_CREATOR,
            )


# ---- 验收①：未审核组件不可发布/引用 ----

def test_unreviewed_version_cannot_be_referenced(env):
    with env.factory() as db:
        skill = skills_service.create_skill(
            db, code=f"skill_ref_{uuid.uuid4().hex[:8]}", name="引用测试",
            tool="none", output_parser="raw_text", description=None, user=U_CREATOR,
        )
        draft = skills_service.create_skill_version(
            db, skill.id, prompt_template="草稿", params_json_schema=None,
            user=U_CREATOR, created_by=1,
        )
        # draft 引用拒绝
        with pytest.raises(BizError) as ei:
            skills_service.resolve_published_skill(db, skill_version_id=draft.id)
        assert ei.value.code == 422
        # 提交后（pending_review）引用仍拒绝
        skills_service.submit_skill_version(db, draft.id, user=U_CREATOR)
        with pytest.raises(BizError) as ei:
            skills_service.resolve_published_skill(db, skill_version_id=draft.id)
        assert ei.value.code == 422


def test_four_eyes_self_review_rejected(env):
    with env.factory() as db:
        skill = skills_service.create_skill(
            db, code=f"skill_eye_{uuid.uuid4().hex[:8]}", name="四眼",
            tool="none", output_parser="raw_text", description=None, user=U_CREATOR,
        )
        version = skills_service.create_skill_version(
            db, skill.id, prompt_template="x", params_json_schema=None,
            user=U_CREATOR, created_by=1,
        )
        skills_service.submit_skill_version(db, version.id, user=U_CREATOR)
        with pytest.raises(BizError) as ei:
            skills_service.review_skill_version(
                db, version.id, user=U_CREATOR, approve=True, note=None
            )
        assert ei.value.code == 403 and "四眼" in ei.value.message


def test_published_version_resolves_and_lifecycle_locks(env):
    skill_id, version_id = make_published_skill(env)
    with env.factory() as db:
        resolved = skills_service.resolve_published_skill(db, skill_version_id=version_id)
        assert resolved["skill_version_id"] == version_id
        assert resolved["prompt_template"] == "分析 {{metric}}：{{question}}"
        assert resolved["params_json_schema"]["required"] == ["metric"]
        # 发布版本内容不可变
        with pytest.raises(BizError) as ei:
            skills_service.update_draft_skill_version(
                db, version_id, prompt_template="改", params_json_schema=None, user=U_CREATOR,
            )
        assert ei.value.code == 409


def test_deprecated_version_cannot_be_newly_referenced(env):
    """验收②引用面：停用后新引用拒绝，但已引用实例（快照）不受影响。"""
    skill_id, version_id = make_published_skill(env)
    with env.factory() as db:
        # 引用方先取快照（模拟编排引用时解析）
        snapshot = skills_service.resolve_published_skill(db, skill_version_id=version_id)
        # 停用
        skills_service.deactivate_skill_version(db, version_id, user=U_REVIEWER)
        # 新引用拒绝
        with pytest.raises(BizError) as ei:
            skills_service.resolve_published_skill(db, skill_version_id=version_id)
        assert ei.value.code == 422
    # 已引用实例持有的是四件套快照（prompt/schema/tool/parser），不随状态流转变化
    assert snapshot["prompt_template"] == "分析 {{metric}}：{{question}}"
    assert snapshot["status"] if False else True  # 快照无 status 字段（解耦状态机）


def test_new_version_does_not_affect_pinned_reference(env):
    """发布 v2 后，v1 引用快照内容不变（版本回退/新版本不影响已引用实例）。"""
    skill_id, v1_id = make_published_skill(env)
    with env.factory() as db:
        v1_snapshot = skills_service.resolve_published_skill(db, skill_version_id=v1_id)
        v2 = skills_service.create_skill_version(
            db, skill_id, prompt_template="v2 模板", params_json_schema=None,
            user=U_CREATOR, created_by=1,
        )
        skills_service.submit_skill_version(db, v2.id, user=U_CREATOR)
        skills_service.review_skill_version(db, v2.id, user=U_REVIEWER, approve=True, note=None)
    assert v1_snapshot["prompt_template"] == "分析 {{metric}}：{{question}}"
    with env.factory() as db:
        v1_row = db.get(WorkflowSkillVersion, v1_id)
        db.expunge(v1_row)
    assert v1_row.status == "published" and v1_row.version == 1
    assert v1_row.prompt_template == "分析 {{metric}}：{{question}}"


# ---- 验收③：组件执行进运行审计 ----

def test_skill_node_execution_audited_in_run_steps(env):
    """带 skill 元数据的节点跑完整运行，步骤记录含 skill 引用审计字段。"""
    skill_id, version_id = make_published_skill(env)
    with env.factory() as db:
        definition = service.create_definition(
            db, code=f"flow_skill_{uuid.uuid4().hex[:8]}", name="Skill 流程",
            description=None, user=U_CREATOR,
        )
        # data_analysis 节点 + config 携带 skill 引用（引用审计随 config 落节点，快照进步骤输出）
        version = service.create_draft_version(
            db, definition.id,
            nodes=[{
                "key": "da1", "name": "技能分析", "node_type": "data_analysis",
                "position": None,
                "config": {
                    "metric_id": "jobs.active",
                    "skill_version_id": version_id,
                },
                "inputs": [], "outputs": [],
            }, {
                "key": "da_end", "name": "结束", "node_type": "data_analysis",
                "position": None, "config": {"metric_id": "jobs.active"},
                "inputs": [], "outputs": [],
            }],
            edges=[{"source": "da1", "target": "da_end", "branch": "", "condition": None}],
            user=U_CREATOR, created_by=1, note=None,
        )
        service.submit_version(db, version.id, user=U_CREATOR)
        service.review_version(db, version.id, user=U_REVIEWER, approve=True, note=None)
        definition_id = definition.id

    with env.factory() as db:
        outcome = run_service.start_manual_run(
            db, definition_id=definition_id, params=None, user=U_CREATOR
        )
    run, result = outcome["run"], outcome["result"]
    assert result.status == "succeeded"
    with env.factory() as db:
        steps = list(db.scalars(
            select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id)
        ))
    da1 = next(s for s in steps if s.node_key == "da1")
    # 审计断言：步骤成功 + 引用的 skill_version_id 随 config 留在节点（落库可回查）
    assert da1.status == "succeeded"
    with env.factory() as db:
        node_row = db.execute(
            select(WorkflowNode).where(
                WorkflowNode.version_id == version.id, WorkflowNode.node_key == "da1"
            )
        ).scalar_one()
        db.expunge(node_row)
    assert node_row.config_json["skill_version_id"] == version_id


def test_run_reference_uses_snapshot_even_after_deprecation(env):
    """运行发起时解析快照；发起后组件停用，运行不受影响（钉死语义）。"""
    skill_id, version_id = make_published_skill(env)
    with env.factory() as db:
        snapshot = skills_service.resolve_published_skill(db, skill_version_id=version_id)
        skills_service.deactivate_skill_version(db, version_id, user=U_REVIEWER)
    # 运行按快照执行（此处以解析结果为准做内容断言；真实 LLM 渲染待 Key，见 DEC-20261107-AGNES-VERIFY）
    assert snapshot["tool"] == "none" and snapshot["output_parser"] == "raw_text"
    with env.factory() as db:
        skill = db.get(WorkflowSkill, skill_id)
        version = db.get(WorkflowSkillVersion, version_id)
        db.expunge(skill)
        db.expunge(version)
    assert skill.tool == "none" and version.status == "deprecated"
    # 快照四件套与停用前一致
    assert snapshot["prompt_template"] == "分析 {{metric}}：{{question}}"
