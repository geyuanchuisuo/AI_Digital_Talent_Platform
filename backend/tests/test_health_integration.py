"""L2 集成测试 · health 真实探测本机 MySQL / Milvus（不主动外呼 LLM/Embedding）。

按开发日志 §1.2：L2 是模块级链路验证；本用例只连本地依赖，不调用任何 LLM。
若本机服务未启动则 skip（不制造假失败），但 B0 已就绪时应全绿。
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from app.api import health as health_api
from app.config import settings
from app.main import app


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.integration
def test_health_real_dependencies():
    if not _port_open(settings.mysql_host, settings.mysql_port):
        pytest.skip(f"MySQL {settings.mysql_host}:{settings.mysql_port} 未启动")
    if not _port_open("localhost", 19530):
        pytest.skip("Milvus 19530 未启动")

    health_api._embedding_alive = None
    health_api.set_worker_state(alive=False, last_heartbeat=None)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()

        assert body["db"] is True, "MySQL 服务连通性探测应为 true"
        assert body["milvus"] is True, "Milvus 服务连通性探测应为 true"
        assert body["milvus_version"], "应返回 Milvus 服务端版本"
        assert body["llm_configured"] is True
        assert body["embedding_configured"] is True
        assert body["embedding_alive"] is None, "health 不得主动调用 Embedding"
        assert body["status"] == "ok"
        assert "code" not in body and "message" not in body and "data" not in body
    finally:
        health_api._embedding_alive = None
