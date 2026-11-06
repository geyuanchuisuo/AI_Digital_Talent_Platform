"""T-12 L2：TaskRunner 调度行为（真实 MySQL，不外呼 LLM/Milvus）。

覆盖 todolist T-12 验收点：
1. 连续两轮扫描同一任务只被消费一次（不重复认领）
2. 两个 runner 同时扫同一行，只有一个抢到（乐观锁）
3. 启动无条件把 `running` 重置 `pending` 且保留 `stage`
4. 心跳超时的 `running` 运行期恢复，且保留 `stage` 断点
5. `duplicate_pending` / `cancelled` / `done` 永不被拉起
6. 并发数从 `sys_settings` 读取并在空闲时重建线程池（1–5 生效）
7. 并发上限生效：槽位满时余下任务留 pending，下一轮再认领
8. handler 未兜住的异常落 `failed`，不会永远 running
9. 优雅停止超时：任务回置 `pending` 并保留 `stage`
10. backlog 统计含 duplicate_pending / cancelled
11. lifespan 启停全局单例

隔离：任务行用随机 id 段插入（`resume_file_id` 等无物理外键），fixture 结束按 id 删除；
`sys_settings.task_concurrency` 用完恢复原值，避免影响其他用例。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import MatchBatch, ParseTask, ScreenBatch, SysSetting
from app.tasks.runner import TaskRunner

pytestmark = pytest.mark.integration

BASE_FILE_ID = 9_000_000


def _now() -> datetime:
    return datetime.now()


def _unique_file_id() -> int:
    return BASE_FILE_ID + int(uuid4().hex[:7], 16)


@pytest.fixture()
def created():
    """登记本用例产生的行，结束删除，避免污染真实库。"""
    rows: dict[str, list[int]] = {"parse": [], "screen": [], "match": []}
    yield rows

    with SessionLocal() as db:
        if rows["parse"]:
            db.execute(delete(ParseTask).where(ParseTask.id.in_(rows["parse"])))
        if rows["screen"]:
            db.execute(delete(ScreenBatch).where(ScreenBatch.id.in_(rows["screen"])))
        if rows["match"]:
            db.execute(delete(MatchBatch).where(MatchBatch.id.in_(rows["match"])))
        db.commit()


@pytest.fixture()
def make_parse(created):
    def _make(
        *,
        status: str = "pending",
        stage: str | None = None,
        heartbeat_at: datetime | None = None,
    ) -> int:
        with SessionLocal() as db:
            row = ParseTask(
                resume_file_id=_unique_file_id(),
                status=status,
                stage=stage,
                retries=0,
                truncated=0,
                heartbeat_at=heartbeat_at,
            )
            db.add(row)
            db.commit()
            created["parse"].append(row.id)
            return row.id

    return _make


@pytest.fixture()
def make_screen(created):
    def _make(*, status: str = "pending") -> int:
        with SessionLocal() as db:
            row = ScreenBatch(
                job_profile_id=1,
                job_profile_version=1,
                config_snapshot_json={},
                scope_json={"type": "all"},
                status=status,
                total=0,
                created_by=1,
            )
            db.add(row)
            db.commit()
            created["screen"].append(row.id)
            return row.id

    return _make


@pytest.fixture()
def make_match(created):
    def _make(*, status: str = "pending") -> int:
        with SessionLocal() as db:
            row = MatchBatch(
                job_profile_id=1,
                job_profile_version=1,
                config_snapshot_json={},
                talent_scope_json={"type": "all"},
                status=status,
                total=0,
                created_by=1,
            )
            db.add(row)
            db.commit()
            created["match"].append(row.id)
            return row.id

    return _make


@pytest.fixture()
def concurrency():
    """临时改 `sys_settings.task_concurrency`，用完恢复原值。"""
    with SessionLocal() as db:
        original = db.execute(
            select(SysSetting.setting_value).where(SysSetting.setting_key == "task_concurrency")
        ).scalar_one_or_none()

    def _set(value: int) -> None:
        with SessionLocal() as db:
            result = db.execute(
                update(SysSetting)
                .where(SysSetting.setting_key == "task_concurrency")
                .values(setting_value=str(value))
            )
            if result.rowcount == 0:
                db.add(SysSetting(setting_key="task_concurrency", setting_value=str(value)))
            db.commit()
        settings.load_runtime_overrides()
        assert settings.task_concurrency == value

    yield _set

    with SessionLocal() as db:
        if original is None:
            db.execute(delete(SysSetting).where(SysSetting.setting_key == "task_concurrency"))
        else:
            db.execute(
                update(SysSetting)
                .where(SysSetting.setting_key == "task_concurrency")
                .values(setting_value=original)
            )
        db.commit()
    settings.load_runtime_overrides()


@pytest.fixture()
def runner():
    instance = TaskRunner()
    yield instance
    instance.stop(timeout=1)


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _status(model, task_id: int) -> tuple[str, str | None]:
    with SessionLocal() as db:
        row = db.get(model, task_id)
        return row.status, getattr(row, "stage", None)


def _succeed(model):
    def _handler(task_id: int) -> None:
        with SessionLocal() as db:
            db.execute(
                update(model).where(model.id == task_id).values(status="success", finished_at=_now())
            )
            db.commit()

    return _handler


# ---------------------------------------------------------------- 认领


def test_each_task_claimed_once_across_cycles(runner, make_parse, concurrency):
    concurrency(2)
    task_ids = [make_parse(), make_parse()]
    seen: list[int] = []

    def handler(task_id: int) -> None:
        seen.append(task_id)
        _succeed(ParseTask)(task_id)

    runner.register("parse", handler)
    assert runner.run_once() == 2
    assert runner.run_once() == 0
    runner.stop(timeout=5)

    assert sorted(seen) == sorted(task_ids)
    for task_id in task_ids:
        assert _status(ParseTask, task_id)[0] == "success"


def test_second_runner_cannot_reclaim_running_task(runner, make_parse, concurrency):
    concurrency(2)
    task_id = make_parse()
    release = threading.Event()

    def handler(_task_id: int) -> None:
        release.wait(10)

    runner.register("parse", handler)
    assert runner.run_once() == 1

    other = TaskRunner()
    other.register("parse", lambda _tid: None)
    assert other.run_once() == 0, "status=running 的任务不能再被第二个 runner 认领"
    other.stop(timeout=1)

    release.set()
    runner.stop(timeout=5)
    assert _status(ParseTask, task_id)[0] == "running"  # handler 不写终态，保持原状


def test_concurrency_limit_leaves_rest_pending(runner, make_parse, concurrency):
    concurrency(2)
    task_ids = [make_parse() for _ in range(4)]
    release = threading.Event()
    started: list[int] = []

    def handler(task_id: int) -> None:
        started.append(task_id)
        release.wait(10)
        _succeed(ParseTask)(task_id)

    runner.register("parse", handler)
    assert runner.run_once() == 2, "默认并发 2，一轮最多认领 2 个"
    assert _wait_until(lambda: len(started) == 2)

    statuses = [_status(ParseTask, task_id)[0] for task_id in task_ids]
    assert statuses.count("running") == 2
    assert statuses.count("pending") == 2

    release.set()
    assert _wait_until(lambda: [_status(ParseTask, tid)[0] for tid in started].count("success") == 2)
    assert _wait_until(lambda: runner.snapshot()["inflight"] == 0)

    assert runner.run_once() == 2, "槽位释放后的下一轮认领余下两个"
    assert _wait_until(
        lambda: all(_status(ParseTask, task_id)[0] == "success" for task_id in task_ids), timeout=10
    )
    runner.stop(timeout=5)


# ---------------------------------------------------------------- 恢复


def test_startup_resets_running_and_keeps_stage(runner, make_parse):
    task_id = make_parse(status="running", stage="vector")
    runner.start()
    runner.stop(timeout=1)

    status, stage = _status(ParseTask, task_id)
    assert status == "pending", "启动必须无条件把 running 重置为 pending"
    assert stage == "vector", "必须保留 stage 作为断点续跑起点"


def test_stale_heartbeat_recovered_with_stage(runner, make_parse):
    task_id = make_parse(
        status="running",
        stage="profile",
        heartbeat_at=_now() - timedelta(seconds=settings.task_heartbeat_timeout + 60),
    )
    stages: list[str | None] = []

    def handler(tid: int) -> None:
        with SessionLocal() as db:
            stages.append(db.get(ParseTask, tid).stage)
            db.execute(
                update(ParseTask).where(ParseTask.id == tid).values(status="success", finished_at=_now())
            )
            db.commit()

    runner.register("parse", handler)
    assert runner.run_once() == 1, "心跳超时的 running 应被恢复并重新认领"
    runner.stop(timeout=5)
    assert stages == ["profile"], "恢复后仍从原 stage 续跑"


def test_fresh_heartbeat_is_not_recovered(runner, make_parse):
    task_id = make_parse(status="running", stage="vector", heartbeat_at=_now())
    runner.register("parse", lambda _tid: None)
    assert runner.run_once() == 0
    assert _status(ParseTask, task_id)[0] == "running"


# ---------------------------------------------------------------- 不扫描的状态


def test_manual_and_cancelled_tasks_never_claimed(runner, make_parse, make_screen, make_match):
    parse_ids = {
        "duplicate_pending": make_parse(status="duplicate_pending"),
        "cancelled": make_parse(status="cancelled"),
        "failed": make_parse(status="failed"),
    }
    screen_id = make_screen(status="done")
    match_id = make_match(status="cancelled")

    calls: list[tuple[str, int]] = []
    for kind in ("parse", "screen", "match"):
        runner.register(kind, lambda tid, kind=kind: calls.append((kind, tid)))

    assert runner.run_once() == 0
    assert calls == []

    for expected, task_id in parse_ids.items():
        assert _status(ParseTask, task_id)[0] == expected
    assert _status(ScreenBatch, screen_id)[0] == "done"
    assert _status(MatchBatch, match_id)[0] == "cancelled"


# ---------------------------------------------------------------- 失败兜底


def test_handler_exception_marks_failed(runner, make_parse, concurrency):
    concurrency(2)
    task_id = make_parse()

    def boom(_tid: int) -> None:
        raise ValueError("boom in handler")

    runner.register("parse", boom)
    runner.run_once()
    runner.stop(timeout=5)

    with SessionLocal() as db:
        row = db.get(ParseTask, task_id)
        assert row.status == "failed"
        assert "boom in handler" in (row.error or "")
        assert row.finished_at is not None


# ---------------------------------------------------------------- 并发配置


def test_concurrency_change_applies_when_idle(runner, concurrency):
    concurrency(2)
    runner.run_once()
    assert runner.snapshot()["concurrency"] == 2

    concurrency(4)
    runner.run_once()
    assert runner.snapshot()["concurrency"] == 4, "空闲时下一周期应重建为 4"

    concurrency(1)
    runner.run_once()
    assert runner.snapshot()["concurrency"] == 1


def test_concurrency_change_defers_while_busy(runner, make_parse, concurrency):
    """D-C：有任务在跑时不重建线程池，等空闲后的下一周期再换。"""
    concurrency(2)
    task_id = make_parse()
    release = threading.Event()

    def handler(tid: int) -> None:
        release.wait(10)
        _succeed(ParseTask)(tid)

    runner.register("parse", handler)
    assert runner.run_once() == 1

    concurrency(4)
    runner.run_once()  # 有在跑任务 → 不重建
    assert runner.snapshot()["concurrency"] == 2, "有任务在跑时必须等其结束"

    release.set()
    assert _wait_until(lambda: runner.snapshot()["inflight"] == 0)
    runner.run_once()
    assert runner.snapshot()["concurrency"] == 4, "空闲后的下一周期生效"
    assert _status(ParseTask, task_id)[0] == "success"


# ---------------------------------------------------------------- backlog 与生命周期


def test_backlog_counts_include_manual_and_cancelled(
    runner, make_parse, make_screen, make_match, concurrency
):
    concurrency(2)
    runner.run_once()
    before = runner.snapshot()["backlog"]

    make_parse()  # pending
    make_parse(status="duplicate_pending")
    make_parse(status="cancelled")
    make_screen()  # pending
    make_match()  # pending

    runner.run_once()  # 未注册 handler → 不认领，只统计
    after = runner.snapshot()["backlog"]

    assert after["parse"] - before["parse"] == 1
    assert after["duplicate_pending"] - before["duplicate_pending"] == 1
    assert after["cancelled"] - before["cancelled"] == 1
    assert after["screen"] - before["screen"] == 1
    assert after["match"] - before["match"] == 1
    assert set(after) == {"parse", "screen", "match", "duplicate_pending", "cancelled"}


def test_graceful_stop_timeout_returns_task_to_pending(runner, make_parse, concurrency):
    concurrency(1)
    task_id = make_parse(stage="vector")
    release = threading.Event()

    def slow(_tid: int) -> None:
        release.wait(10)

    runner.register("parse", slow)
    assert runner.run_once() == 1

    started = time.monotonic()
    runner.stop(timeout=0.2)
    elapsed = time.monotonic() - started
    assert elapsed < 5, "停止不能无限等 handler"
    assert _status(ParseTask, task_id) == ("pending", "vector"), "超时任务回置 pending 且保留 stage"

    release.set()


@pytest.mark.real_runner
def test_lifespan_starts_and_stops_singleton():
    from app.tasks.runner import runner as singleton

    with TestClient(app):
        assert singleton.is_running is True
    assert singleton.is_running is False
