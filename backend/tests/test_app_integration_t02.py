"""L2 集成测试 · T-02 在**生产 app** 上的接线验证。

验证 `/api/health` 仍是唯一例外、404 走统一错误体、request_id 与响应头一致、
以及四类处理器确实注册到了 `app.main:app`。

D02（Master 统一执行表，2026-11-10）：追加「限流环境开关」单元用例——
默认演示模式保持 `/api` 全量豁免；客户模式打开后 429 / 403 / 降级放行
与健康检查豁免逐项验证。不修改 D 归属的 RateLimitMiddleware / CacheClient。
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.core.cache.client import CacheClient
from app.core.cache.ratelimit import DEFAULT_EXEMPT_PATHS, RateLimitMiddleware
from app.core.errors import BizError
from app.main import app, rate_limit_exempt_paths


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


# --------------------------------------------------------------------------- #
# D02-A：限流环境开关（A 侧交付；D 的中间件/缓存实现不改）
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """最小异步 Redis 替身，只覆盖限流链路用到的 exists/set/incr/ttl/expire。"""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def exists(self, name: str) -> int:
        return 1 if name in self.kv else 0

    async def set(self, name: str, value: str, ex: int | None = None) -> bool:
        self.kv[name] = value
        if ex is not None:
            self.ttls[name] = ex
        return True

    async def incr(self, name: str) -> int:
        value = int(self.kv.get(name, 0)) + 1
        self.kv[name] = str(value)
        return value

    async def ttl(self, name: str) -> int:
        return self.ttls.get(name, -1) if name in self.kv else -2

    async def expire(self, name: str, seconds: int) -> bool:
        self.ttls[name] = seconds
        return True


def _limited_app(cache: CacheClient, *, exempt_paths: tuple[str, ...]) -> FastAPI:
    """按 main.py 的挂载方式构造独立 app，注入可控 cache 与豁免路径。"""
    mini = FastAPI()

    @mini.get("/api/ping")
    async def ping() -> dict:
        return {"ok": True}

    @mini.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    mini.add_middleware(
        RateLimitMiddleware,
        cache=cache,
        limit=3,
        window_seconds=60,
        exempt_paths=exempt_paths,
    )
    return mini


def test_d02_demo_mode_keeps_full_api_exemption(monkeypatch):
    """默认（RATE_LIMIT_ENABLED=false）：/api 全量豁免，历史行为零变化。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    paths = rate_limit_exempt_paths()
    assert paths == (*DEFAULT_EXEMPT_PATHS, "/api")
    assert "/api" in paths

    cache = CacheClient("redis://fake", client=_FakeRedis())
    client = TestClient(_limited_app(cache, exempt_paths=paths))
    # 超过 limit 3 次也不 429，证明豁免确实生效
    for _ in range(5):
        assert client.get("/api/ping", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
    assert client.get("/api/health").status_code == 200


def test_d02_customer_mode_exempts_only_health(monkeypatch):
    """客户模式：只豁免 /api/health，业务路径进入限流判定。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    paths = rate_limit_exempt_paths()
    assert paths == DEFAULT_EXEMPT_PATHS
    assert "/api" not in paths
    assert "/api/health" in paths


def test_d02_customer_mode_429_with_retry_after(monkeypatch):
    """客户模式：固定窗口超限返回 429 + Retry-After，换身份不受牵连。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    cache = CacheClient("redis://fake", client=_FakeRedis())
    client = TestClient(_limited_app(cache, exempt_paths=rate_limit_exempt_paths()))

    for _ in range(3):
        assert client.get("/api/ping", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
    resp = client.get("/api/ping", headers={"X-Forwarded-For": "9.9.9.9"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert resp.json()["code"] == 429 and resp.json()["data"] is None
    assert "request_id" in resp.json()
    assert client.get("/api/ping", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200
    # 健康检查始终豁免，不受计数影响
    assert client.get("/api/health").status_code == 200


def test_d02_customer_mode_blacklist_403(monkeypatch):
    """客户模式：黑名单 403 优先于限流，且不牵连其他身份。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    fake = _FakeRedis()
    cache = CacheClient("redis://fake", client=fake)
    asyncio.run(cache.set("ratelimit", "blacklist:6.6.6.6", {"reason": "abuse"}, ttl=3600))
    client = TestClient(_limited_app(cache, exempt_paths=rate_limit_exempt_paths()))

    resp = client.get("/api/ping", headers={"X-Forwarded-For": "6.6.6.6"})
    assert resp.status_code == 403 and resp.json()["code"] == 403
    assert client.get("/api/ping", headers={"X-Forwarded-For": "5.5.5.5"}).status_code == 200
    assert client.get("/api/health").status_code == 200


def test_d02_customer_mode_redis_down_degrades_to_200(monkeypatch):
    """客户模式：Redis 不可用时降级放行，核心接口仍 200（V2.6 §7）。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    # 指向死端口且无注入 client：真实连接失败 → CacheClient 返回 None → 限流放行
    cache = CacheClient("redis://127.0.0.1:1/0")
    client = TestClient(_limited_app(cache, exempt_paths=rate_limit_exempt_paths()))
    assert client.get("/api/ping").status_code == 200
    assert client.get("/api/health").status_code == 200
