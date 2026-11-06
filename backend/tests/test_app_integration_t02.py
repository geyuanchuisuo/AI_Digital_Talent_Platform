"""L2 集成测试 · T-02 在**生产 app** 上的接线验证。

验证 `/api/health` 仍是唯一例外、404 走统一错误体、request_id 与响应头一致、
以及四类处理器确实注册到了 `app.main:app`。
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.errors import BizError
from app.main import app


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.integration
def test_health_is_still_the_only_unwrapped_success(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "code" not in body and "message" not in body and "data" not in body
    assert "request_id" not in body


@pytest.mark.integration
def test_production_not_found_uses_unified_error(client):
    resp = client.get("/api/not-a-route")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == 404
    assert body["data"] is None
    assert "detail" not in body
    assert body["request_id"].startswith("req-")
    assert resp.headers["X-Request-ID"] == body["request_id"]


@pytest.mark.integration
def test_request_log_masking_still_applies(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        resp = client.get("/api/health?phone=13800000000")
    assert resp.status_code == 200
    assert "138****0000" in caplog.text
    assert "13800000000" not in caplog.text


def test_all_handlers_registered_on_production_app():
    """T-02 的挂载点确实接上了 main.py，不是只在独立测试 app 里生效。"""
    assert BizError in app.exception_handlers
