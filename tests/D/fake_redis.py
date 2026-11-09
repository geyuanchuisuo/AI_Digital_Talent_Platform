"""内存版 redis.asyncio 子集（T-D01 单测替身）。

实现 CacheClient 依赖的最小异步接口：ping/get/set/delete/exists/incr/expire/
ttl/dbsize/aclose。TTL 用可注入时钟（默认 time.monotonic），测试可直接拨快。
生产代码不引用本文件。
"""

from __future__ import annotations

import time
from typing import Callable


class FakeRedis:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._data: dict[str, str] = {}
        self._expire_at: dict[str, float] = {}
        self._clock = clock or time.monotonic
        self.closed = False

    # ---- 内部 ----

    def _alive(self, name: str) -> bool:
        exp = self._expire_at.get(name)
        if exp is not None and exp <= self._clock():
            self._data.pop(name, None)
            self._expire_at.pop(name, None)
        return name in self._data

    def _now(self) -> float:
        return self._clock()

    # ---- 异步接口 ----

    async def ping(self) -> bool:
        return True

    async def get(self, name: str) -> str | None:
        return self._data.get(name) if self._alive(name) else None

    async def set(self, name: str, value: str, ex: int | None = None) -> bool:
        self._data[name] = value
        if ex:
            self._expire_at[name] = self._now() + ex
        else:
            self._expire_at.pop(name, None)
        return True

    async def delete(self, *names: str) -> int:
        removed = 0
        for name in names:
            if self._alive(name):
                self._data.pop(name, None)
                self._expire_at.pop(name, None)
                removed += 1
        return removed

    async def exists(self, *names: str) -> int:
        return sum(1 for name in names if self._alive(name))

    async def incr(self, name: str) -> int:
        current = int(self._data.get(name, "0")) if self._alive(name) else 0
        current += 1
        self._data[name] = str(current)
        return current

    async def expire(self, name: str, seconds: int) -> bool:
        if not self._alive(name):
            return False
        self._expire_at[name] = self._now() + seconds
        return True

    async def ttl(self, name: str) -> int:
        """-2 不存在 / -1 无过期 / >=0 剩余秒（向上取整到 1）。"""
        if not self._alive(name):
            return -2
        exp = self._expire_at.get(name)
        if exp is None:
            return -1
        remaining = exp - self._now()
        return max(1, int(remaining))

    async def dbsize(self) -> int:
        return sum(1 for name in list(self._data) if self._alive(name))

    async def aclose(self) -> None:
        self.closed = True
