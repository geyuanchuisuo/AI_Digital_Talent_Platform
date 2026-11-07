"""工作流引擎用例（T-D04 验收①②③）。

①示例图跑通分支/条件/等待/恢复四路径；②杀进程（新建引擎实例）后从检查点续跑、
等待态不被重置；③恢复机制绝不触碰 waiting，与 TaskRunner 互不干扰。
运行：pytest tests/D/test_workflow_engine.py -v（SQLite 文件库真持久化，无外部依赖）。
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from typing_extensions import TypedDict

from app.core.workflows.engine.registry import recover_registry, register_run
from app.core.workflows.engine.registry import mark_status as _mark_status
from app.models import WorkflowEngineRun
from app.core.workflows.engine.runner import WorkflowEngine
from app.core.workflows.engine.saver import make_saver

# conftest 自动加载（SQLite 编译规则）；本文件全部同步用例


class FlowState(TypedDict, total=False):
    score: int
    path: str
    approved: bool
    final: str


def build_review_graph() -> StateGraph:
    """示例图（验收①）：分支/条件/人工等待/恢复。

    START → classify（条件边）→ 高分走 express → wait_approval（interrupt 挂起）
    → approve → END；低分走 normal → END。
    """

    def classify(state: FlowState) -> dict:
        return {"path": "express" if state["score"] >= 60 else "normal"}

    def wait_approval(state: FlowState) -> dict:
        answer = interrupt({"reason": "高分申请需人工审批", "score": state["score"]})
        return {"approved": bool(answer.get("approved"))}

    def approve(state: FlowState) -> dict:
        return {"final": "approved" if state.get("approved") else "rejected"}

    def normal(state: FlowState) -> dict:
        return {"final": "auto-passed"}

    def route(state: FlowState) -> str:
        return "wait_approval" if state["path"] == "express" else "normal"

    g = StateGraph(FlowState)
    g.add_node("classify", classify)
    g.add_node("wait_approval", wait_approval)
    g.add_node("approve", approve)
    g.add_node("normal", normal)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route, ["wait_approval", "normal"])
    g.add_edge("wait_approval", "approve")
    g.add_edge("approve", END)
    g.add_edge("normal", END)
    return g


@pytest.fixture()
def saver_url(tmp_path):
    return f"sqlite:///{tmp_path / 'checkpoints.db'}"


def _new_engine(url: str, registry_factory=None) -> WorkflowEngine:
    return WorkflowEngine(make_saver(url), registry_factory=registry_factory)


# ---- 验收①：分支 / 条件 / 等待 / 恢复 ----

def test_branch_condition_normal_path(saver_url):
    engine = _new_engine(saver_url)
    result = engine.start("review", build_review_graph(), {"score": 42}, thread_id="t-low")
    assert result.status == "succeeded"
    assert result.values["path"] == "normal"
    assert result.values["final"] == "auto-passed"


def test_express_branch_waits_then_resumes(saver_url):
    engine = _new_engine(saver_url)
    waiting = engine.start("review", build_review_graph(), {"score": 88}, thread_id="t-high")
    # 等待路径：人工审批挂起，带 interrupt 负载
    assert waiting.status == "waiting"
    assert waiting.interrupt == {"reason": "高分申请需人工审批", "score": 88}
    assert waiting.values["path"] == "express"

    resumed = engine.resume("review", build_review_graph(), "t-high", {"approved": True})
    assert resumed.status == "succeeded"
    assert resumed.values["final"] == "approved"


# ---- 验收②：杀进程（新实例）后检查点续跑、等待态不重置 ----

def test_wait_state_survives_engine_restart_and_is_not_reset(saver_url):
    """验收②核心：interrupt 挂起后「杀掉进程」（新建全部对象，仅共享检查点文件），
    等待态必须原样保留；不 resume 反复查询也不重置；最终恢复成功。"""
    engine1 = _new_engine(saver_url)
    waiting = engine1.start("review", build_review_graph(), {"score": 88}, thread_id="t-kill")
    assert waiting.status == "waiting"

    # ——「杀进程」：全新引擎实例（新 saver 对象、同 SQLite 文件）——
    engine2 = _new_engine(saver_url)
    state = engine2.state("review", build_review_graph(), "t-kill")
    assert state["next"] == ["wait_approval"]  # 仍停在人工节点
    assert state["values"]["path"] == "express"  # 检查点续跑上下文完整

    # 不 resume 重复查询：等待态不被任何机制重置/推进
    for _ in range(3):
        again = engine2.state("review", build_review_graph(), "t-kill")
        assert again["next"] == ["wait_approval"]

    # 新实例直接完成恢复（断点续跑）
    resumed = engine2.resume("review", build_review_graph(), "t-kill", {"approved": False})
    assert resumed.status == "succeeded"
    assert resumed.values["final"] == "rejected"


def test_checkpoint_saver_roundtrip_persistence(saver_url):
    """底层保证：put/get_tuple/put_writes 跨 saver 实例一致。"""
    saver1 = make_saver(saver_url)
    engine1 = WorkflowEngine(saver1)
    engine1.start("review", build_review_graph(), {"score": 88}, thread_id="t-rt")
    first = saver1.get_tuple({"configurable": {"thread_id": "t-rt"}})
    assert first is not None and first.checkpoint["id"]

    saver2 = make_saver(saver_url)  # 新实例读同一文件
    second = saver2.get_tuple({"configurable": {"thread_id": "t-rt"}})
    assert second.checkpoint["id"] == first.checkpoint["id"]
    assert second.checkpoint["channel_values"]["path"] == "express"


# ---- 验收③：恢复机制不触碰 waiting；与 TaskRunner 表隔离 ----

@pytest.fixture()
def registry_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'registry.db'}")
    Base = WorkflowEngineRun.__table__.metadata
    Base.create_all(engine, tables=[WorkflowEngineRun.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_recover_never_resets_waiting(registry_factory):
    register_run(registry_factory, graph_name="review", thread_id="th-wait")
    register_run(registry_factory, graph_name="review", thread_id="th-run")

    # th-wait 进入人工等待；th-run 假设进程被杀留在 running
    _mark_status(registry_factory, "th-wait", "waiting")
    summary = recover_registry(registry_factory)
    assert summary["recovered"] == 1  # 只有孤儿 running 被 置 failed
    assert summary["waiting"] == 1

    with registry_factory() as db:
        wait_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == "th-wait")
        ).scalar_one()
        run_row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == "th-run")
        ).scalar_one()
    assert wait_row.status == "waiting"  # **验收②核心断言**：恢复不重置等待态
    assert wait_row.error is None
    assert run_row.status == "failed" and run_row.finished_at is not None  # 可重试


def test_engine_failure_marks_registry_failed(registry_factory, saver_url):
    def boom(state: FlowState) -> dict:
        raise RuntimeError("节点炸了")

    g = StateGraph(FlowState)
    g.add_node("boom", boom)
    g.add_edge(START, "boom")
    g.add_edge("boom", END)

    engine = _new_engine(saver_url, registry_factory=registry_factory)
    result = engine.start("boomflow", g, {"score": 1}, thread_id="th-boom")
    assert result.status == "failed" and "节点炸了" in result.error
    with registry_factory() as db:
        row = db.execute(
            select(WorkflowEngineRun).where(WorkflowEngineRun.thread_id == "th-boom")
        ).scalar_one()
    assert row.status == "failed" and "节点炸了" in row.error  # 回退边界：failed 可重试
