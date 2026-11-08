"""接口限流/黑名单中间件（T-D01）。

口径（docs/modules/D/缓存限流契约.md）：
- 纯 ASGI 中间件，身份取 X-Forwarded-For 首个 IP，缺失回退 client host；
- 固定窗口计数（`ratelimit:count:{identity}`）超 `limit` → 429 + Retry-After；
- 黑名单（`ratelimit:blacklist:{identity}`）存在 → 403；
- **降级**：缓存未启用/不可用 → 一律放行（V2.6：Redis 停服降级直查库，
  核心接口仍 200），并按间隔打 warning 防日志刷屏；
- 429 不在统一错误码八值白名单内，属于传输层限流响应（A 侧确认登记，见挂载请求）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.config import settings
from app.core.cache.client import CacheClient, get_cache

logger = logging.getLogger(__name__)

DEFAULT_EXEMPT_PATHS = ("/api/health",)
_DEGRADE_WARN_INTERVAL = 30.0  # 秒；降级 warning 最小间隔


class RateLimitMiddleware:
    """用法：`app.add_middleware(RateLimitMiddleware)`（挂载归 A）。

    测试可注入 cache（CacheClient 实例，可包 FakeRedis）与 limit/window。
    """

    def __init__(
        self,
        app: Any,
        cache: CacheClient | None = None,
        *,
        limit: int | None = None,
        window_seconds: int | None = None,
        exempt_paths: tuple[str, ...] = DEFAULT_EXEMPT_PATHS,
    ) -> None:
        self.app = app
        self.cache = cache
        self.limit = limit if limit is not None else settings.rate_limit_per_minute
        self.window = window_seconds if window_seconds is not None else settings.rate_limit_window_seconds
        self.exempt_paths = exempt_paths
        self._last_degrade_warn = 0.0

    # ---- ASGI ----

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith(self.exempt_paths):
            await self.app(scope, receive, send)
            return

        cache = self.cache if self.cache is not None else get_cache()
        if not cache.enabled:
            await self.app(scope, receive, send)
            return

        identity = self._identity(scope)

        if await cache.exists("ratelimit", f"blacklist:{identity}"):
            await self._send_json(
                send,
                status=403,
                body={"code": 403, "message": "client blacklisted", "data": None},
                scope=scope,
            )
            return

        count = await cache.incr_window("ratelimit", f"count:{identity}", self.window)
        if count is None:
            self._warn_degraded()
            await self.app(scope, receive, send)
            return

        if count > self.limit:
            await self._send_json(
                send,
                status=429,
                body={"code": 429, "message": "rate limit exceeded", "data": None},
                scope=scope,
                headers={"Retry-After": str(max(1, self.window))},
            )
            return

        await self.app(scope, receive, send)

    # ---- helpers ----

    @staticmethod
    def _identity(scope: dict) -> str:
        """X-Forwarded-For 首个 IP 优先（部署在反代后时由代理保证首段为真实客户端）。"""
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        xff = headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _warn_degraded(self) -> None:
        now = time.monotonic()
        if now - self._last_degrade_warn >= _DEGRADE_WARN_INTERVAL:
            self._last_degrade_warn = now
            logger.warning("RateLimitMiddleware 降级：Redis 不可用，限流放行（%ds 后再提示）", _DEGRADE_WARN_INTERVAL)

    @staticmethod
    async def _send_json(
        send: Any,
        *,
        status: int,
        body: dict,
        scope: dict,
        headers: dict[str, str] | None = None,
    ) -> None:
        request_id = (scope.get("state") or {}).get("request_id") if scope.get("state") else None
        if not request_id:
            from app.utils.ids import new_request_id

            request_id = new_request_id()
        payload = {**body, "request_id": request_id}
        out_headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"x-request-id", request_id.encode("utf-8")),
            *[(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in (headers or {}).items()],
        ]
        await send({"type": "http.response.start", "status": status, "headers": out_headers})
        await send(
            {
                "type": "http.response.body",
                "body": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            }
        )
