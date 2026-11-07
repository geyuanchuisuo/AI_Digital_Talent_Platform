"""RateLimitMiddleware 用例（T-D01 验收②）：429 / 黑名单 403 / 降级放行 / 豁免路径。

降级反例用「指向死端口的真实 CacheClient」而非 fake，验证真实连接失败路径。
全部 async（模块级 anyio 标记）；TestClient 可在 async 用例内直接调用。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cache.client import CacheClient
from app.core.cache.ratelimit import RateLimitMiddleware
from fake_redis import FakeRedis

pytestmark = pytest.mark.anyio


def make_app(cache, *, limit: int = 3, window: int = 60) -> FastAPI:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, cache=cache, limit=limit, window_seconds=window)
    return app


async def test_ratelimit_429_with_retry_after():
    client = TestClient(make_app(CacheClient("redis://fake", client=FakeRedis()), limit=3))
    for _ in range(3):
        assert client.get("/api/ping", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
    resp = client.get("/api/ping", headers={"X-Forwarded-For": "9.9.9.9"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    body = resp.json()
    assert body["code"] == 429 and "request_id" in body and body["data"] is None
    # 换身份不受影响
    assert client.get("/api/ping", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200


async def test_blacklist_returns_403():
    fake = FakeRedis()
    cache = CacheClient("redis://fake", client=fake)
    await cache.set("ratelimit", "blacklist:6.6.6.6", {"reason": "abuse"}, ttl=3600)

    client = TestClient(make_app(cache))
    resp = client.get("/api/ping", headers={"X-Forwarded-For": "6.6.6.6"})
    assert resp.status_code == 403
    assert resp.json()["code"] == 403
    # 黑名单命中优先于限流，TTL 内持续拦截
    assert client.get("/api/ping", headers={"X-Forwarded-For": "6.6.6.6"}).status_code == 403
    # 其他身份不受牵连
    assert client.get("/api/ping", headers={"X-Forwarded-For": "5.5.5.5"}).status_code == 200


async def test_health_exempt_from_limit():
    cache = CacheClient("redis://fake", client=FakeRedis())
    client = TestClient(make_app(cache, limit=1))
    assert client.get("/api/ping").status_code == 200
    assert client.get("/api/ping").status_code == 429
    # 豁免路径不受限流影响，始终可达
    for _ in range(5):
        assert client.get("/api/health").status_code == 200


async def test_degraded_redis_passes_through_core_200():
    """降级反例（验收②）：Redis 指向死端口 → 连接失败 → 放行，核心接口仍 200。"""
    dead = CacheClient("redis://127.0.0.1:9/0", probe_cooldown=60.0)  # discard 端口，必拒
    client = TestClient(make_app(dead, limit=1))
    for _ in range(10):  # 远超 limit：降级下限流不生效
        resp = client.get("/api/ping", headers={"X-Forwarded-For": "7.7.7.7"})
        assert resp.status_code == 200
    stats = dead.stats()
    assert stats["errors"] >= 1 and stats["degraded_ops"] >= 1
    assert stats["available"] is False  # 进入探测冷却


async def test_blacklist_degrades_open_when_redis_down():
    """降级反例补充：黑名单检查在 Redis 不可用时也必须放行（不得误杀）。"""
    dead = CacheClient("redis://127.0.0.1:9/0", probe_cooldown=60.0)
    client = TestClient(make_app(dead))
    assert client.get("/api/ping", headers={"X-Forwarded-For": "7.7.7.7"}).status_code == 200


async def test_unconfigured_redis_disables_middleware():
    """未配置 REDIS_URL（enabled=False）：中间件直通，不产生任何限流行为。"""
    disabled = CacheClient("")
    client = TestClient(make_app(disabled, limit=1))
    for _ in range(10):
        assert client.get("/api/ping").status_code == 200
    assert disabled.stats()["degraded_ops"] == 0  # 直通路径连降级计数都不进
