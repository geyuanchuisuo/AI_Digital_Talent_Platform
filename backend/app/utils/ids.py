"""请求 ID 生成（T-01 中间件与 T-02 异常处理器共用，避免两套实现）。

格式：`req-` + ULID；依赖缺失时退化为 uuid4 hex。
"""

from __future__ import annotations

try:
    from ulid import ULID

    def new_request_id() -> str:
        return "req-" + str(ULID())

except ImportError:  # pragma: no cover - 正常路径不会走到
    import uuid

    def new_request_id() -> str:
        return "req-" + uuid.uuid4().hex
