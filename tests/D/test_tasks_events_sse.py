"""D17 任务进度 SSE 边界用例（T-D-SELF-03）。

SQLite 内存库隔离；覆盖：
- 权限反例（他人解析任务 / 个人账号看筛选匹配批次 → 403 JSON，不进流）；
- 终态一致性（终态事件只发一次、流随即关闭、失败原因可见）；
- 重连语义（Last-Event-ID 去重、未收到终态时补发）；
- 流内权限同步失效（任务被转移 / 账号停用 → 明确 error 事件后关闭）；
- 降级（快照读取失败 → 明确 unavailable，不伪造进度）。

运行：backend/.venv/Scripts/python.exe -m pytest tests/D/test_tasks_events_sse.py -v
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module
from app.api.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.core.tasks import events as events_module
from app.database import Base, get_db
from app.models import MatchBatch, ParseTask, ResumeFile, ScreenBatch, User

U_HR = SimpleNamespace(id=2, role="hr", person_id=None)
U_OWNER = SimpleNamespace(id=3, role="personal", person_id=1)
U_OTHER = SimpleNamespace(id=4, role="personal", person_id=2)

# 测试用主键（SQLite 无自增依赖，显式给 id 更稳）
RESUME_ID = 1
PARSE_ID = 10
SCREEN_ID = 20
MATCH_ID = 30
JOB_ID = 100


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__, ResumeFile.__table__, ParseTask.__table__,
            ScreenBatch.__table__, MatchBatch.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            User(id=2, username="hr", password_hash="x", role="hr", is_active=1),
            User(id=3, username="owner", password_hash="x", role="personal",
                 person_id=1, is_active=1),
            User(id=4, username="other", password_hash="x", role="personal",
                 person_id=2, is_active=1),
        ])
        db.commit()

    # events.py 与 scope.py 各自持有 SessionLocal；都指向测试库
    monkeypatch.setattr(events_module, "SessionLocal", factory)
    monkeypatch.setattr(database_module, "SessionLocal", factory)
    monkeypatch.setattr(events_module, "POLL_INTERVAL_SECONDS", 0)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(events_module.router)

    def override_db():
        db2 = factory()
        try:
            yield db2
        finally:
            db2.close()

    app.dependency_overrides[get_db] = override_db
    yield SimpleNamespace(engine=engine, factory=factory, app=app, monkeypatch=monkeypatch)
    engine.dispose()


def _as(client: TestClient, user) -> TestClient:
    client.app.dependency_overrides[get_current_user] = lambda: user
    return client


def _seed_parse(env, *, status="pending", uploaded_by=3, error=None):
    with env.factory() as db:
        db.add(ResumeFile(
            id=RESUME_ID, filename="a.pdf", stored_path="/x", file_hash="h1",
            file_ext="pdf", file_size=1, upload_batch_id="b1", uploaded_by=uploaded_by,
        ))
        db.add(ParseTask(id=PARSE_ID, resume_file_id=RESUME_ID, status=status, error=error))
        db.commit()


def _seed_screen(env, *, status="pending", error=None):
    with env.factory() as db:
        db.add(ScreenBatch(
            id=SCREEN_ID, job_profile_id=JOB_ID, job_profile_version=1,
            config_snapshot_json={}, scope_json={}, status=status,
            progress=0, total=0, passed=0, pending_count=0, rejected=0,
            retry_count=0, vector_degraded=0, created_by=2, error=error,
        ))
        db.commit()


def _seed_match(env, *, status="pending", error=None):
    with env.factory() as db:
        db.add(MatchBatch(
            id=MATCH_ID, job_profile_id=JOB_ID, job_profile_version=1,
            config_snapshot_json={}, talent_scope_json={}, status=status,
            progress=0, total=0, hard_failed=0, retry_count=0,
            vector_degraded=0, created_by=2, error=error,
        ))
        db.commit()


def _stream(env, user, kind: str, task_id: int, headers: dict | None = None):
    client = _as(TestClient(env.app), user)
    with client.stream("GET", f"/api/tasks/{task_id}/events?kind={kind}",
                       headers=headers or {}) as resp:
        return resp.status_code, resp.headers.get("content-type", ""), resp.read().decode("utf-8")


# ---- 权限反例 ----

def test_parse_task_other_user_forbidden(env):
    _seed_parse(env)
    status, ctype, _ = _stream(env, U_OTHER, "parse", PARSE_ID)
    assert status == 403
    assert ctype.startswith("application/json")  # 鉴权先于流，不进 SSE


def test_parse_task_missing_returns_404(env):
    status, ctype, _ = _stream(env, U_OWNER, "parse", 9999)
    assert status == 404
    assert ctype.startswith("application/json")


def test_screen_task_personal_forbidden(env):
    _seed_screen(env)
    status, ctype, _ = _stream(env, U_OWNER, "screen", SCREEN_ID)
    assert status == 403
    assert ctype.startswith("application/json")


def test_match_task_personal_forbidden(env):
    _seed_match(env)
    status, ctype, _ = _stream(env, U_OWNER, "match", MATCH_ID)
    assert status == 403
    assert ctype.startswith("application/json")


def test_owner_can_subscribe_parse(env):
    _seed_parse(env, status="success")
    status, ctype, body = _stream(env, U_OWNER, "parse", PARSE_ID)
    assert status == 200
    assert ctype.startswith("text/event-stream")
    assert "event: done" in body


def test_hr_can_subscribe_screen(env):
    _seed_screen(env, status="done")
    status, ctype, body = _stream(env, U_HR, "screen", SCREEN_ID)
    assert status == 200
    assert ctype.startswith("text/event-stream")
    assert "event: done" in body


# ---- 终态一致性 ----

def test_terminal_event_sent_once_and_stream_closes(env):
    _seed_parse(env, status="success")
    _, _, body = _stream(env, U_OWNER, "parse", PARSE_ID)
    assert body.count("event: done") == 1
    assert "event: progress" not in body


def test_terminal_failed_includes_error_detail(env):
    _seed_parse(env, status="failed", error="PDF 解析失败")
    _, _, body = _stream(env, U_OWNER, "parse", PARSE_ID)
    assert "event: done" in body
    assert "PDF 解析失败" in body


def test_screen_terminal_failed_includes_error(env):
    _seed_screen(env, status="failed", error="向量服务不可用")
    _, _, body = _stream(env, U_HR, "screen", SCREEN_ID)
    assert "event: done" in body
    assert "向量服务不可用" in body


# ---- 重连语义 ----

def test_last_event_id_hit_is_not_resent(env):
    _seed_parse(env, status="success")
    with env.factory() as db:
        data = events_module.snapshot(db, "parse", PARSE_ID, U_OWNER.id, False)
    fid = events_module.event_id(data)

    _, _, body = _stream(env, U_OWNER, "parse", PARSE_ID, headers={"Last-Event-ID": fid})
    assert "event:" not in body  # 已收到终态：去重，不重发


def test_terminal_resent_when_last_event_id_is_stale(env):
    _seed_parse(env, status="pending")
    with env.factory() as db:
        progress = events_module.snapshot(db, "parse", PARSE_ID, U_OWNER.id, False)
    stale_fid = events_module.event_id(progress)

    # 断线期间任务完成：重连时 Last-Event-ID 仍是旧的 progress 指纹
    with env.factory() as db:
        db.get(ParseTask, PARSE_ID).status = "success"
        db.commit()

    _, _, body = _stream(env, U_OWNER, "parse", PARSE_ID, headers={"Last-Event-ID": stale_fid})
    assert "event: done" in body  # 终态补发
    assert body.count("event: done") == 1


# ---- 流内权限同步失效 ----

def test_task_transferred_mid_stream_yields_error(env, monkeypatch):
    _seed_parse(env, status="pending")
    original = events_module.snapshot
    calls = {"n": 0}

    def flaky(db, kind, task_id, user_id, management):
        calls["n"] += 1
        if calls["n"] >= 2:  # 初始快照已用掉第 1 次；流内再读时任务已被转移
            db.get(ResumeFile, RESUME_ID).uploaded_by = 99
            db.commit()
        return original(db, kind, task_id, user_id, management)

    monkeypatch.setattr(events_module, "snapshot", flaky)
    _, _, body = _stream(env, U_OWNER, "parse", PARSE_ID)
    assert "event: progress" in body
    assert "event: error" in body
    assert "forbidden" in body


def test_account_disabled_mid_stream_yields_error(env, monkeypatch):
    _seed_parse(env, status="pending")
    original = events_module.snapshot
    calls = {"n": 0}

    def flaky(db, kind, task_id, user_id, management):
        calls["n"] += 1
        if calls["n"] >= 2:
            db.get(User, U_OWNER.id).is_active = 0
            db.commit()
        return original(db, kind, task_id, user_id, management)

    monkeypatch.setattr(events_module, "snapshot", flaky)
    _, _, body = _stream(env, U_OWNER, "parse", PARSE_ID)
    assert "event: progress" in body
    assert "event: error" in body
    assert "account_disabled" in body


# ---- 降级 ----

def test_snapshot_failure_mid_stream_yields_unavailable(env, monkeypatch):
    _seed_parse(env, status="pending")
    original = events_module.snapshot
    calls = {"n": 0}

    def flaky(db, kind, task_id, user_id, management):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("db down")
        return original(db, kind, task_id, user_id, management)

    monkeypatch.setattr(events_module, "snapshot", flaky)
    _, _, body = _stream(env, U_OWNER, "parse", PARSE_ID)
    assert "event: progress" in body
    assert "event: error" in body
    assert "unavailable" in body
    # 降级不伪造进度：error 之后没有 progress
    assert body.rfind("event: error") > body.rfind("event: progress")
