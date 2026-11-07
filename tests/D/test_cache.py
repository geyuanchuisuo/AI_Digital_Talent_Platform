"""CacheClient 单测（T-D01 验收①③）：三类键/序列化/TTL/命中率。

真实 Redis 集成用例见文件尾 `test_real_redis_roundtrip`：
默认连 `REDIS_TEST_URL`（redis://127.0.0.1:6399/0），不可达自动 skip。
本机起测试 Redis：docker run -d --name aitalent-test-redis -p 6399:6379 redis:7-alpine
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest

from app.core.cache.client import KEY_PREFIX, CacheClient, UnknownCacheKind, get_cache, reset_cache
from fake_redis import FakeRedis

# 本文件全部为 async 用例；backend 已装 anyio（FastAPI 依赖），用其 pytest 插件驱动
pytestmark = pytest.mark.anyio


def make_client(clock=None) -> tuple[CacheClient, FakeRedis]:
    fake = FakeRedis(clock=clock)
    client = CacheClient("redis://fake:test/0", client=fake)
    return client, fake


async def test_three_kinds_prefix_and_roundtrip():
    client, fake = make_client()
    for kind in ("session", "data", "ratelimit"):
        await client.set(kind, "u1", {"v": kind, "n": 1}, ttl=60)
        assert fake._data[f"{KEY_PREFIX}:{kind}:u1"] is not None
    assert await client.get("session", "u1") == {"v": "session", "n": 1}
    assert await client.get("data", "u1") == {"v": "data", "n": 1}
    assert await client.get("ratelimit", "u1") == {"v": "ratelimit", "n": 1}
    # 中文/None 字段序列化往返
    await client.set("data", "k2", {"名": "张三", "x": None})
    assert await client.get("data", "k2") == {"名": "张三", "x": None}
    # delete/exists
    assert await client.exists("session", "u1") is True
    assert await client.delete("session", "u1") is True
    assert await client.exists("session", "u1") is False
    assert await client.get("session", "u1") is None


async def test_unknown_kind_rejected():
    client, _ = make_client()
    with pytest.raises(UnknownCacheKind):
        await client.set("oops", "k", 1)


async def test_ttl_expiry_with_injected_clock():
    now = 1000.0
    client, _ = make_client(clock=lambda: now)
    await client.set("session", "s1", {"a": 1}, ttl=30)
    assert await client.get("session", "s1") == {"a": 1}
    now += 31
    assert await client.get("session", "s1") is None
    # ttl 语义：不存在 -2 / 无过期 -1 / 剩余秒
    await client.set("data", "d1", 1, ttl=10)
    assert await client.ttl("data", "d1") >= 1
    await client.set("data", "d2", 1)
    assert await client.ttl("data", "d2") == -1
    assert await client.ttl("data", "absent") == -2


async def test_stats_hit_miss_ratio():
    client, _ = make_client()
    await client.set("data", "hit", {"x": 1})
    await client.get("data", "hit")
    await client.get("data", "nope")
    stats = client.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1
    assert stats["hit_ratio"] == 0.5 and stats["enabled"] is True


async def test_disabled_client_degrades_everything():
    client = CacheClient("")  # 未配置 REDIS_URL
    assert client.enabled is False
    assert await client.get("data", "k") is None
    assert await client.set("data", "k", 1) is False
    assert await client.incr_window("ratelimit", "c", 60) is None
    assert await client.exists("ratelimit", "blacklist:x") is False
    stats = client.stats()
    assert stats["degraded_ops"] == 4 and stats["hit_ratio"] is None


async def test_incr_window_sets_and_selfheals_ttl():
    now = 2000.0
    client, fake = make_client(clock=lambda: now)
    assert await client.incr_window("ratelimit", "count:1.1.1.1", 60) == 1
    key = f"{KEY_PREFIX}:ratelimit:count:1.1.1.1"
    assert fake._expire_at[key] == 2060  # 首次计数即设窗口
    assert await client.incr_window("ratelimit", "count:1.1.1.1", 60) == 2
    # 自愈：人为剥掉 TTL 后再 incr，应补回窗口
    fake._expire_at.pop(key)
    now += 1
    assert await client.incr_window("ratelimit", "count:1.1.1.1", 60) == 3
    assert fake._expire_at[key] == 2061


async def test_get_cache_singleton_reads_settings(monkeypatch):
    reset_cache()
    monkeypatch.setattr("app.config.settings.redis_url", "")
    assert get_cache().enabled is False
    reset_cache()
    monkeypatch.setattr("app.config.settings.redis_url", "redis://localhost:6399/0")
    assert get_cache().enabled is True
    reset_cache()  # 留干净状态给后续用例


# ---- 真实 Redis（可选；不可达自动跳过） ----

REDIS_TEST_URL = "redis://127.0.0.1:6399/0"


def _redis_reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.3):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _redis_reachable(REDIS_TEST_URL), reason="测试 Redis 未运行（6399）")
async def test_real_redis_roundtrip():
    client = CacheClient(REDIS_TEST_URL)
    try:
        await client.set("data", "real:1", {"ok": True}, ttl=60)
        assert await client.get("data", "real:1") == {"ok": True}
        assert await client.incr_window("ratelimit", "real:count", 60) == 1
        assert await client.delete("data", "real:1") is True
    finally:
        await client.aclose()
