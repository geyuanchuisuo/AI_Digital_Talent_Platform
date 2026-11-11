"""T-12 L1：TaskRunner 纯进程内行为（不连数据库）。

只测不依赖 MySQL 的部分：类型校验、快照结构、配置下限保护。调度/认领/恢复属 L2，
见 `test_runner_integration_t12.py`。
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core import worker_state
from app.tasks.runner import KINDS, MAX_CONCURRENCY, MIN_CONCURRENCY, TaskRunner


def test_kinds_cover_legacy_and_fenced_qa_tables():
    assert [kind.name for kind in KINDS] == ["parse", "screen", "match", "profile_qa", "knowledge_ingest", "knowledge_qa", "graph_sync"]
    assert [kind.model.__tablename__ for kind in KINDS] == ["parse_tasks", "screen_batches", "match_batches", "talent_question_jobs", "knowledge_revisions", "knowledge_question_jobs", "graph_sync_jobs"]
    assert [kind.fenced for kind in KINDS] == [False, False, False, True, True, True, True]


def test_register_rejects_unknown_kind():
    runner = TaskRunner()
    with pytest.raises(ValueError, match="未知任务类型"):
        runner.register("pars", lambda _tid: None)


def test_register_accepts_all_known_kinds():
    runner = TaskRunner()
    for kind in (item.name for item in KINDS):
        runner.register(kind, lambda *args: None)


def test_snapshot_has_health_shape_before_start():
    worker_state.reset_worker_state()
    runner = TaskRunner()
    snapshot = runner.snapshot()

    assert snapshot["alive"] is False
    assert snapshot["concurrency"] == 0
    assert snapshot["inflight"] == 0
    assert set(snapshot["backlog"]) == set(worker_state.BACKLOG_KEYS)


def test_scan_interval_has_floor(monkeypatch):
    monkeypatch.setattr(settings, "runner_scan_interval", 0.0)
    assert TaskRunner._scan_interval() == 0.1, "0 间隔会变成忙等，必须有下限"
    monkeypatch.setattr(settings, "runner_scan_interval", 2.5)
    assert TaskRunner._scan_interval() == 2.5


def test_shutdown_and_heartbeat_timeouts_have_floor(monkeypatch):
    monkeypatch.setattr(settings, "task_shutdown_timeout", 0)
    monkeypatch.setattr(settings, "task_heartbeat_timeout", 0)
    assert TaskRunner._shutdown_timeout() == 1
    assert TaskRunner._heartbeat_timeout() == 1


def test_concurrency_bounds_are_documented_range():
    assert (MIN_CONCURRENCY, MAX_CONCURRENCY) == (1, 5), "运行配置 v1.3：1–5"
