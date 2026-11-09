"""tests/D 冒烟测试（T-D00f 验收）——证明 harness 三件套可用。

覆盖：假 LLM 三态切换 / 事件工厂 12 类与幂等键 / SSE 解析 / SQLite fixture。
"""
from __future__ import annotations

import httpx
import pytest
from openai import APITimeoutError, OpenAI

from app.core.llm_client import LLMError
from events_factory import EVENT_TYPES, build_all_events, build_event, idempotency_key
from fake_llm import FakeLLMServer
from sse_client import parse_sse, parse_sse_data

SHORT_TIMEOUT = 0.5  # 秒；timeout 态靠 server 挂起 3s 触发


@pytest.fixture()
def fake_llm():
    server = FakeLLMServer("success")
    yield server
    server.stop()


def _client(server: FakeLLMServer) -> OpenAI:
    return OpenAI(base_url=server.base_url, api_key="test-key", timeout=SHORT_TIMEOUT, max_retries=0)


def test_fake_llm_success_state(fake_llm):
    fake_llm.set_mode("success")
    resp = _client(fake_llm).chat.completions.create(
        model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "fake-ok"
    assert fake_llm.requests, "server 应记录收到的请求体"


def test_fake_llm_timeout_state():
    server = FakeLLMServer("timeout")
    try:
        with pytest.raises((APITimeoutError, httpx.TimeoutException, LLMError)):
            _client(server).chat.completions.create(
                model="fake-model", messages=[{"role": "user", "content": "hi"}])
    finally:
        server.stop()


def test_fake_llm_malformed_state(fake_llm):
    fake_llm.set_mode("malformed")
    with pytest.raises((LLMError, KeyError, IndexError, AttributeError, TypeError)):
        resp = _client(fake_llm).chat.completions.create(
            model="fake-model", messages=[{"role": "user", "content": "hi"}])
        # 兼容 SDK 不抛错直接返回空 choices 的情况
        resp.choices[0].message.content  # noqa: B018


def test_event_factory_twelve_types_unique_keys():
    events = build_all_events(ref_id=7)
    assert len(events) == len(EVENT_TYPES) == 14
    keys = [e["idempotency_key"] for e in events.values()]
    assert len(set(keys)) == len(keys), "幂等键必须唯一"
    for et, e in events.items():
        assert e["idempotency_key"] == idempotency_key(et, ref_id=7, version=1)
        assert e["ref_id"] == 7
        assert e["status"] == "pending"


def test_event_factory_rejects_unknown_type():
    with pytest.raises(ValueError):
        build_event("unknown.event")


def test_sse_parser_blocks_and_data():
    raw = "event: stage\ndata: {\"step\": 1}\n\nevent: done\ndata: final\n\n"
    events = parse_sse(raw)
    assert [e["event"] for e in events] == ["stage", "done"]
    assert parse_sse_data(events) == ['{"step": 1}', "final"]


def test_sqlite_fixture_roundtrip(tmp_path):
    """DB fixture 冒烟：SQLite 建表 + ORM 写读往返。"""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.database import Base
    from app.models import user as user_mod  # noqa: F401 触发表注册

    engine = create_engine(f"sqlite:///{tmp_path / 'test_d.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = user_mod.User(username="t-d", password_hash="x", role="hr")
        session.add(row)
        session.commit()
        found = session.scalar(select(user_mod.User).where(user_mod.User.username == "t-d"))
        assert found is not None and found.role == "hr"
    engine.dispose()
