"""工作流受控生命周期用例（T-D15 验收①②）。

①草稿→提交→审核发布/驳回→停用→回滚 全生命周期；
②无审核权限者提交后不可自行发布：四眼原则（提交人 ≠ 审核人，403）+
   个人账号全操作 403。
运行：pytest tests/D/test_workflow_lifecycle.py -v（SQLite 文件库，无外部依赖）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.errors import BizError
from app.models import (
    SysRole,
    SysUserRole,
    User,
    WorkflowDefinition,
    WorkflowDefinitionVersion,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from app.modules.workflows import service

# conftest 自动加载（SQLite 编译规则）；本文件全部同步用例

# sys_roles/sys_user_roles 建空表：get_role_codes 的 sys_user_roles 查询须成功返回空
# 才回退 users.role（P3 修复，见 UPD-MASTER-20261110-203044；缺表=查询异常=一律 personal）。
WF_TABLES = (
    User.__table__,
    SysRole.__table__,
    SysUserRole.__table__,
    WorkflowDefinition.__table__,
    WorkflowDefinitionVersion.__table__,
    WorkflowNode.__table__,
    WorkflowEdge.__table__,
    WorkflowRun.__table__,
)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'wf_lifecycle.db'}")
    WorkflowDefinition.__table__.metadata.create_all(engine, tables=WF_TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    # 门禁 require_management_guard 内部开 app.database.SessionLocal（真实库）——
    # 重定向到本测试库，角色判定走替身用户（P3 修复）。
    monkeypatch.setattr("app.database.SessionLocal", factory)
    with factory() as session:
        yield session
    engine.dispose()


_seq = 0


def _user(db, role: str) -> User:
    global _seq
    _seq += 1
    user = User(
        username=f"wf_{role}_{_seq}", password_hash="x", role=role, is_active=1
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _graph(nodes_count: int = 2):
    types = ("profile_parse", "job_match", "data_analysis")
    nodes = [
        {"key": f"n{i + 1}", "name": f"节点{i + 1}", "node_type": types[i % len(types)]}
        for i in range(nodes_count)
    ]
    edges = [
        {"source": f"n{i + 1}", "target": f"n{i + 2}"} for i in range(max(0, nodes_count - 1))
    ]
    return nodes, edges


def _submitted_draft(db, author: User, nodes_count: int = 2):
    """作者建定义 + 草稿并提交审核；返回 (definition, version)。"""
    definition = service.create_definition(
        db, code=f"wf_lifecycle_{author.id}", name="生命周期流程", user=author
    )
    nodes, edges = _graph(nodes_count)
    version = service.create_draft_version(
        db, definition.id, nodes=nodes, edges=edges, user=author, created_by=author.id
    )
    version = service.submit_version(db, version.id, user=author)
    return definition, version


# ---- 验收①：全生命周期 ----


def test_full_lifecycle_submit_review_publish_deactivate_rollback(db):
    hr_a = _user(db, "hr")
    hr_b = _user(db, "hr")
    definition, v1 = _submitted_draft(db, hr_a)
    assert v1.status == "pending_review" and v1.submitted_by == hr_a.id
    assert v1.submitted_at is not None

    # hr_b 审核通过 → published（published_by=审核人）
    v1 = service.review_version(db, v1.id, user=hr_b, approve=True, note="图结构合理")
    assert v1.status == "published" and v1.published_by == hr_b.id
    assert v1.review_note == "图结构合理"

    # 发布版可启动运行（钉死 v1）
    run = service.start_run(db, definition_id=definition.id, thread_id="th-life-1", created_by=hr_a.id)
    assert run.version_id == v1.id

    # v2 提交并审核通过 → 停用 → 回滚到 v1
    nodes2, edges2 = _graph(1)
    v2 = service.create_draft_version(db, definition.id, nodes=nodes2, edges=edges2, user=hr_a)
    v2 = service.submit_version(db, v2.id, user=hr_a)
    v2 = service.review_version(db, v2.id, user=hr_b, approve=True)
    assert v2.status == "published"

    v2 = service.deactivate_published_version(db, v2.id, user=hr_b)
    assert v2.status == "deprecated"

    # 回滚到 v1：唯一发布版回到 v1；新运行缺省取 v1（运行中的旧实例不受影响）
    target = service.rollback_definition(db, definition.id, target_version_id=v1.id, user=hr_b)
    assert target.id == v1.id and target.status == "published"
    run2 = service.start_run(db, definition_id=definition.id, thread_id="th-life-2")
    assert run2.version_id == v1.id

    # 回滚到一个已停用版本（v2）→ 重新发布并成为唯一发布版
    target2 = service.rollback_definition(db, definition.id, target_version_id=v2.id, user=hr_b)
    assert target2.id == v2.id and target2.status == "published"
    states = {
        v.version: v.status
        for v in db.execute(
            select(WorkflowDefinitionVersion).where(
                WorkflowDefinitionVersion.definition_id == definition.id
            )
        ).scalars()
    }
    assert states == {1: "deprecated", 2: "published"}
    run3 = service.start_run(db, definition_id=definition.id, thread_id="th-life-3")
    assert run3.version_id == v2.id


def test_reject_returns_to_draft_with_note_then_resubmit(db):
    hr_a = _user(db, "hr")
    hr_b = _user(db, "hr")
    definition, v1 = _submitted_draft(db, hr_a)

    v1 = service.review_version(db, v1.id, user=hr_b, approve=False, note="缺人工审批节点")
    assert v1.status == "draft" and v1.review_note == "缺人工审批节点"
    assert v1.published_at is None

    # 修改后重新提交并通过
    nodes, edges = _graph(3)
    v1 = service.update_draft_version(db, v1.id, nodes=nodes, edges=edges, user=hr_a)
    v1 = service.submit_version(db, v1.id, user=hr_a)
    v1 = service.review_version(db, v1.id, user=hr_b, approve=True, note="复核通过")
    assert v1.status == "published"

    # 详情可审阅：审核三字段回显（T-D15 交接给审核页）
    detail = service.get_version_detail(db, v1.id)
    assert detail["version"]["submitted_by"] == hr_a.id
    assert detail["version"]["review_note"] == "复核通过"
    assert detail["version"]["published_by"] == hr_b.id


def test_empty_graph_cannot_submit_or_review(db):
    hr_a = _user(db, "hr")
    hr_b = _user(db, "hr")
    definition = service.create_definition(db, code="wf_empty", name="空图", user=hr_a)
    version = service.create_draft_version(db, definition.id, nodes=[], edges=[], user=hr_a)
    with pytest.raises(BizError, match="空图不可提交"):
        service.submit_version(db, version.id, user=hr_a)
    # 绕过提交直接造待审（服务原语）后审核也会拦空图
    version.status = "pending_review"
    db.commit()
    with pytest.raises(BizError, match="空图不可发布"):
        service.review_version(db, version.id, user=hr_b, approve=True)


# ---- 验收②：无审核权限者提交后不可自行发布（403）----


def test_submitter_cannot_self_review_or_publish(db):
    """四眼原则：提交人 approve/reject 自己的版本都 403，版本保持待审。"""
    hr_a = _user(db, "hr")
    hr_b = _user(db, "hr")
    definition, v1 = _submitted_draft(db, hr_a)

    for approve in (True, False):
        with pytest.raises(BizError) as excinfo:
            service.review_version(db, v1.id, user=hr_a, approve=approve)
        assert excinfo.value.code == 403 and "四眼" in excinfo.value.message
    db.refresh(v1)
    assert v1.status == "pending_review"  # 未被自己动过

    # 换人审核放行（对照）
    v1 = service.review_version(db, v1.id, user=hr_b, approve=True)
    assert v1.status == "published"


def test_personal_cannot_operate_anything(db):
    hr_a = _user(db, "hr")
    personal = _user(db, "personal")
    definition, v1 = _submitted_draft(db, hr_a)

    with pytest.raises(BizError) as create_exc:
        service.create_definition(db, code="wf_p_1", name="个人建流程", user=personal)
    assert create_exc.value.code == 403
    with pytest.raises(BizError) as submit_exc:
        service.submit_version(db, v1.id, user=personal)
    assert submit_exc.value.code == 403
    with pytest.raises(BizError) as review_exc:
        service.review_version(db, v1.id, user=personal, approve=True)
    assert review_exc.value.code == 403
    with pytest.raises(BizError) as rb_exc:
        service.rollback_definition(db, definition.id, target_version_id=v1.id, user=personal)
    assert rb_exc.value.code == 403
    with pytest.raises(BizError) as deact_exc:
        service.deactivate_published_version(db, v1.id, user=personal)
    assert deact_exc.value.code == 403
    db.refresh(v1)
    assert v1.status == "pending_review"


def test_review_state_and_rollback_guard_rails(db):
    hr_a = _user(db, "hr")
    hr_b = _user(db, "hr")
    definition, v1 = _submitted_draft(db, hr_a)

    # 未提交（draft）不可审核；重复提交/停用/回滚目标非法各 409/422
    draft = service.create_draft_version(db, definition.id, nodes=_graph(1)[0], edges=[], user=hr_a)
    with pytest.raises(BizError, match="仅待审核版本可审核"):
        service.review_version(db, draft.id, user=hr_b, approve=True)
    with pytest.raises(BizError, match="仅草稿可提交"):
        service.submit_version(db, v1.id, user=hr_b)  # 已是 pending_review
    with pytest.raises(BizError, match="仅发布版本可"):
        service.deactivate_published_version(db, v1.id, user=hr_b)
    with pytest.raises(BizError, match="须为已发布/已停用"):
        service.rollback_definition(db, definition.id, target_version_id=draft.id, user=hr_b)
    with pytest.raises(BizError, match="不属于该流程定义"):
        other = service.create_definition(db, code="wf_other", name="别的流程", user=hr_b)
        ov = service.create_draft_version(db, other.id, nodes=_graph(1)[0], edges=[], user=hr_b)
        service.rollback_definition(db, definition.id, target_version_id=ov.id, user=hr_b)
    # v1 审核通过后停用，可回滚；重复回滚同目标幂等
    v1 = service.review_version(db, v1.id, user=hr_b, approve=True)
    service.deactivate_published_version(db, v1.id, user=hr_b)
    first = service.rollback_definition(db, definition.id, target_version_id=v1.id, user=hr_b)
    second = service.rollback_definition(db, definition.id, target_version_id=v1.id, user=hr_b)
    assert first.id == second.id == v1.id and second.status == "published"
