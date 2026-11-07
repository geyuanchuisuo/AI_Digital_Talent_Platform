"""缓存客户端（T-D01）。

设计口径（docs/modules/D/缓存限流契约.md）：
- 三类键前缀隔离：`aitalent:{kind}:{key}`，kind ∈ {session, data, ratelimit}；
  黑名单挂在 ratelimit 类（`ratelimit:blacklist:{identity}`）。
- 值一律 JSON 序列化（ensure_ascii=False），跨语言/跨实例可读。
- **降级语义**：未配置 REDIS_URL、连接失败或探测冷却期内，所有操作按
  「缓存不存在」处理——读返回 None（调用方直查库）、写静默跳过、限流放行。
  绝不让缓存故障演变为接口故障。
- 探测冷却：一次连接失败后 `probe_cooldown` 秒内不再真实触网，避免高 QPS 下
  每请求都等连接超时。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from app.config import settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "aitalent"
KINDS = ("session", "data", "ratelimit")


class UnknownCacheKind(ValueError):
    """kind 不在三类白名单内（防前缀拼错导致键类别混淆）。"""


class CacheClient:
    """redis.asyncio 的降级安全封装。

    测试可注入 `client`（实现 ping/get/set/delete/exists/incr/expire/ttl/dbsize/aclose
    的异步子集，见 tests/D/fake_redis.py），生产用 redis.asyncio 按需懒建。
    """

    def __init__(
        self,
        url: str,
        *,
        client: Any | None = None,
        probe_cooldown: float = 5.0,
    ) -> None:
        self._url = url
        self._enabled = bool(url)
        self._external = client
        self._redis: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unavailable_until = 0.0
        self._probe_cooldown = probe_cooldown
        self._stats = {"hits": 0, "misses": 0, "degraded_ops": 0, "errors": 0}

    # ---- 基础 ----

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def available(self) -> bool:
        """enabled 且不在探测冷却期（不代表真实可达，真实状态以最近一次操作为准）。"""
        return self._enabled and time.monotonic() >= self._unavailable_until

    def _client(self) -> Any:
        if self._external is not None:
            return self._external
        # redis.asyncio 客户端绑定创建时的事件循环：跨循环复用（脚本多次
        # asyncio.run / 测试新建 loop）会 "Event loop is closed"。按运行循环重建。
        loop = asyncio.get_running_loop()
        if self._redis is None or self._loop is not loop:
            import redis.asyncio as aioredis

            self._redis = aioredis.Redis.from_url(
                self._url,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
                decode_responses=True,
            )
            self._loop = loop
        return self._redis

    @staticmethod
    def _k(kind: str, key: str) -> str:
        if kind not in KINDS:
            raise UnknownCacheKind(f"{kind!r} 不在 {KINDS} 内")
        return f"{KEY_PREFIX}:{kind}:{key}"

    def _degraded_now(self) -> bool:
        return not self._enabled or time.monotonic() < self._unavailable_until

    def _mark_unavailable(self, exc: Exception) -> None:
        self._unavailable_until = time.monotonic() + self._probe_cooldown
        self._stats["errors"] += 1
        logger.warning("cache unavailable（%.1fs 冷却后重探）：%s", self._probe_cooldown, exc)

    async def _op(self, fn: Callable[[Any], Any]) -> Any | None:
        """执行一次真实操作；失败标记冷却并返回 None（降级语义由各方法定义）。"""
        try:
            return await fn(self._client())
        except Exception as exc:  # noqa: BLE001 - redis 任何故障都按降级处理
            self._mark_unavailable(exc)
            return None

    # ---- 读 ----

    async def get(self, kind: str, key: str) -> Any | None:
        """取值；未命中/未启用/不可用一律 None。命中率统计只算真实触网。"""
        name = self._k(kind, key)  # kind 非法属调用方 bug，先于降级检查抛出
        if self._degraded_now():
            self._stats["degraded_ops"] += 1
            return None
        raw = await self._op(lambda c: c.get(name))
        if raw is None:
            self._stats["misses"] += 1
            return None
        self._stats["hits"] += 1
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("cache value 非 JSON（kind=%s key=%s），按 miss 处理", kind, key)
            self._stats["hits"] -= 1
            self._stats["misses"] += 1
            return None

    async def exists(self, kind: str, key: str) -> bool:
        name = self._k(kind, key)
        if self._degraded_now():
            self._stats["degraded_ops"] += 1
            return False
        found = await self._op(lambda c: c.exists(name))
        return bool(found)

    async def ttl(self, kind: str, key: str) -> int | None:
        """剩余秒数；未启用/不可用/键不存在返回 None（-1 永不过期、-2 不存在照原样）。"""
        name = self._k(kind, key)
        if self._degraded_now():
            self._stats["degraded_ops"] += 1
            return None
        return await self._op(lambda c: c.ttl(name))

    # ---- 写 ----

    async def set(self, kind: str, key: str, value: Any, *, ttl: int | None = None) -> bool:
        """写值（JSON）；ttl 秒过期。降级/失败返回 False，不抛错。"""
        payload = json.dumps(value, ensure_ascii=False)
        name = self._k(kind, key)
        if self._degraded_now():
            self._stats["degraded_ops"] += 1
            return False
        result = await self._op(lambda c: c.set(name, payload, ex=ttl) if ttl else c.set(name, payload))
        return bool(result)

    async def delete(self, kind: str, key: str) -> bool:
        name = self._k(kind, key)
        if self._degraded_now():
            self._stats["degraded_ops"] += 1
            return False
        result = await self._op(lambda c: c.delete(name))
        return bool(result)

    async def incr_window(self, kind: str, key: str, window_seconds: int) -> int | None:
        """计数 +1 并保证键带窗口 TTL（INCR 为 1 或发现无 TTL 时补 EXPIRE）。

        返回 None 表示未启用/不可用（限流应放行）。INCR 与 EXPIRE 非原子：
        中途宕机可能留下无 TTL 键，靠「ttl==-1 再补 EXPIRE」自愈。
        """
        name = self._k(kind, key)
        if self._degraded_now():
            self._stats["degraded_ops"] += 1
            return None

        def _incr(c: Any) -> Any:
            async def _run() -> Any:
                count = await c.incr(name)
                if count == 1 or await c.ttl(name) == -1:
                    await c.expire(name, window_seconds)
                return count

            return _run()

        return await self._op(_incr)

    # ---- 运维观测 ----

    async def dbsize(self) -> int | None:
        if self._degraded_now():
            self._stats["degraded_ops"] += 1
            return None
        return await self._op(lambda c: c.dbsize())

    def stats(self) -> dict[str, Any]:
        """命中率/降级快照（进程内计数；接入日志或健康接口由消费方决定）。"""
        hits, misses = self._stats["hits"], self._stats["misses"]
        total = hits + misses
        return {
            **self._stats,
            "enabled": self._enabled,
            "available": self.available,
            "hit_ratio": round(hits / total, 4) if total else None,
        }

    async def aclose(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                pass
            self._redis = None


# ---- 进程级单例 ----

_instance: CacheClient | None = None


def get_cache() -> CacheClient:
    """按 settings.redis_url 取进程级实例；未配置时返回 enabled=False 的降级实例。"""
    global _instance
    if _instance is None:
        _instance = CacheClient(settings.redis_url)
    return _instance


def reset_cache(instance: CacheClient | None = None) -> None:
    """测试/重载用：替换或清空单例（替换方负责旧实例 aclose）。"""
    global _instance
    _instance = instance
