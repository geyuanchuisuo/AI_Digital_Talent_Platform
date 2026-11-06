"""L1 单元测试 · /api/health 与请求日志中间件（全部打桩，不触外部服务）。

对齐 `docs/接口设计.md` v1.3 §3 与 `docs/统一错误码与鉴权.md` v1.2 的 health 例外。
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.api import health as health_api
from app.config import settings
from app.main import app

EXPECTED_FIELDS = {
    "status",
    "db",
    "milvus",
    "milvus_version",
    "llm_configured",
    "embedding_configured",
    "embedding_alive",
    "worker",
    "version",
}


@pytest.fixture(autouse=True)
def reset_health_state():
    health_api._embedding_alive = None
    health_api.set_worker_state(alive=False, last_heartbeat=None)
    yield
    health_api._embedding_alive = None


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def probes_ok(monkeypatch):
    monkeypatch.setattr(health_api, "_probe_db", lambda: True)
    monkeypatch.setattr(health_api, "_probe_milvus", lambda: (True, "2.6.24"))


def test_health_shape_and_ok(client, probes_ok):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == EXPECTED_FIELDS  # health 是唯一不套 code/message/data 的接口
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["milvus"] is True
    assert body["milvus_version"] == "2.6.24"
    assert body["llm_configured"] is True
    assert body["embedding_configured"] is True
    assert body["embedding_alive"] is None  # 被动探活：从未调用过为 null
    assert body["version"] == "0.1.0"
    assert body["worker"]["alive"] is False  # T-12 前未启动属预期，不降级
    assert set(body["worker"]["backlog"]) == {"parse", "screen", "match", "duplicate_pending", "cancelled"}


def test_health_degraded_still_200(client, monkeypatch):
    monkeypatch.setattr(health_api, "_probe_db", lambda: False)
    monkeypatch.setattr(health_api, "_probe_milvus", lambda: (False, None))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["db"] is False
    assert body["milvus"] is False
    assert body["milvus_version"] is None


def test_embedding_alive_false_degrades(client, probes_ok):
    health_api.set_embedding_alive(False)
    body = client.get("/api/health").json()
    assert body["embedding_alive"] is False
    assert body["status"] == "degraded"


def test_embedding_alive_true_is_ok(client, probes_ok):
    health_api.set_embedding_alive(True)
    body = client.get("/api/health").json()
    assert body["embedding_alive"] is True
    assert body["status"] == "ok"


def test_worker_state_setter(client, probes_ok):
    backlog = {"parse": 3, "screen": 1, "match": 0, "duplicate_pending": 1, "cancelled": 0}
    health_api.set_worker_state(alive=True, last_heartbeat="2026-11-05T12:00:00", backlog=backlog)
    body = client.get("/api/health").json()
    assert body["worker"]["alive"] is True
    assert body["worker"]["last_heartbeat"] == "2026-11-05T12:00:00"
    assert body["worker"]["backlog"] == backlog
    assert body["status"] == "ok"


def test_request_id_header_and_log_masking(client, probes_ok, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        resp = client.get("/api/health?phone=13800000000&email=alice@example.com")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID", "").startswith("req-")
    assert "138****0000" in caplog.text
    assert "13800000000" not in caplog.text
    assert "a***e@example.com" in caplog.text
    assert "alice@example.com" not in caplog.text


# --------------------------------------------------------------------------- #
# T-05：collection 维度校验（health 字段集合不变，只在结构冲突时判 false）
# --------------------------------------------------------------------------- #
class _FakeMilvus:
    def __init__(self, *, exists: bool, dim: int | None = None) -> None:
        self.exists = exists
        self.dim = dim

    def has_collection(self, name):
        return self.exists

    def describe_collection(self, collection_name):
        return {"fields": [{"name": "vector", "params": {"dim": self.dim}}]}


def test_collection_dim_check_absent_is_ok():
    assert health_api._collection_dim_ok(_FakeMilvus(exists=False)) is True


def test_collection_dim_check_mismatch_is_not_ok():
    assert health_api._collection_dim_ok(_FakeMilvus(exists=True, dim=512)) is False


def test_collection_dim_check_match_is_ok():
    assert health_api._collection_dim_ok(_FakeMilvus(exists=True, dim=settings.embedding_dim)) is True
