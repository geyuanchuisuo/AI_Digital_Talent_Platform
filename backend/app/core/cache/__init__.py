"""统一缓存/会话/限流层（T-D01）。

组成：
- `client.CacheClient`：redis.asyncio 客户端封装——三类键（session/data/ratelimit）
  前缀隔离、JSON 序列化、TTL、不可用探测与冷却、命中率/降级计数。
- `ratelimit.RateLimitMiddleware`：纯 ASGI 中间件——黑名单 403、固定窗口 429、
  **Redis 不可用一律放行降级**（V2.6：停服降级直查库，核心接口仍 200）。

契约：`docs/modules/D/缓存限流契约.md`。
挂载：`main.py` 归 A（见 docs/requests/D/ 限流中间件挂载请求）。
"""

from app.core.cache.client import CacheClient, get_cache, reset_cache
from app.core.cache.ratelimit import RateLimitMiddleware

__all__ = ["CacheClient", "RateLimitMiddleware", "get_cache", "reset_cache"]
