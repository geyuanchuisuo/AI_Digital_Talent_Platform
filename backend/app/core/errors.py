"""统一错误码与全局异常体系（T-02）。

实现口径唯一依据：`docs/统一错误码与鉴权.md` v1.2、`docs/接口设计.md` v1.3 §1。

关键约束：
- 错误体恒为 `{code, message, data: null, request_id}`；**不加 `detail` 字段**。
- `code` 只允许 400/401/403/404/409/422/429/500/503 九个值（外加成功 0），
  不引入 1001/4001 等细分码；`request_id` **仅错误响应携带**。
  429 为 T-D01 限流响应（2026-11-07 用户指令纳入；由 RateLimitMiddleware 直接构造，
  结构与本文件统一错误体一致）。
- `GET /health` 是成功响应的唯一例外（不套统一体），异常时仍走本文件统一错误体。
- **503 只用于「请求尚未产生任何业务写入」**（接口 §1.2）。MySQL 已提交后向量失败
  一律走 200 + `data.vector_status="failed"`，由业务接口用
  `app.core.response.ok(..., message=HALF_WRITE_VECTOR_MESSAGE)` 返回，**不得抛 BizError(503)**。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.ids import new_request_id
from app.utils.mask import mask_sensitive

logger = logging.getLogger("app.error")

SUCCESS_CODE = 0
ALLOWED_ERROR_CODES = frozenset({400, 401, 403, 404, 409, 422, 429, 500, 503})
HALF_WRITE_VECTOR_MESSAGE = "画像已保存，向量同步失败，可稍后重试"

DEFAULT_MESSAGES: dict[int, str] = {
    400: "请求参数或业务校验失败",
    401: "未登录或登录已过期",
    403: "账号已停用",
    404: "记录不存在",
    409: "数据已存在，请勿重复提交",
    422: "请求参数结构不合法",
    429: "请求过于频繁，请稍后重试",
    500: "服务器内部错误",
    503: "服务暂时不可用，请稍后重试",
}

# Starlette 框架自动抛出的英文 detail；文档 §一 要求 message 是中文业务提示
FRAMEWORK_DEFAULT_DETAILS = frozenset(
    {
        "Not Found",
        "Method Not Allowed",
        "Unauthorized",
        "Forbidden",
        "Bad Request",
        "Internal Server Error",
        "Unprocessable Entity",
    }
)


class BizError(Exception):
    """service 层业务异常。`code` 必须是白名单错误码之一。

    文档 §六 建议 401/403/404 用原生 HTTPException，但本实现允许 BizError 使用全部白名单码，
    这样 service 层不必为了状态码而依赖 fastapi；两者最终都归一为同一个错误体。
    """

    def __init__(self, code: int, message: str):
        if code not in ALLOWED_ERROR_CODES:
            raise ValueError(
                f"非法错误码 {code}；只允许 {sorted(ALLOWED_ERROR_CODES)}，不得引入细分码"
            )
        self.code = code
        self.message = message
        self.status_code = code
        super().__init__(message)


def _request_id(request: Request) -> str:
    """优先取 T-01 中间件写入的 request_id；缺失时兜底生成（异常可能早于中间件）。"""
    return getattr(request.state, "request_id", None) or new_request_id()


def error_response(code: int, message: str, request: Request) -> JSONResponse:
    """构造统一错误体；HTTP status 与 code 始终一致。

    响应头也带 `X-Request-ID`：未捕获异常由最外层 ServerErrorMiddleware 处理时，
    T-01 的请求日志中间件来不及写头，这里补齐，保证成功/错误响应排障体验一致。
    """
    request_id = _request_id(request)
    return JSONResponse(
        status_code=code,
        content={
            "code": code,
            "message": message,
            "data": None,
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def _normalize_http_status(status: int) -> int:
    """框架自动产生的非白名单状态（405 / 413 / 429 / 502 ...）归一到八个码内。

    4xx → 400，其余 → 500；保证 `code` 字段不出现细分码，也不引入白名单外的值。
    """
    return 400 if 400 <= status < 500 else 500


def register_exception_handlers(app: FastAPI) -> None:
    """注册四类全局异常处理器；`main.py` 在路由注册前调用一次。"""

    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError) -> JSONResponse:
        return error_response(exc.code, exc.message, request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = exc.status_code if exc.status_code in ALLOWED_ERROR_CODES else _normalize_http_status(exc.status_code)
        # FastAPI 原生 HTTPException 用 detail 传 message；业务自己写的中文文案保留，
        # 框架默认英文文案（Not Found / Method Not Allowed ...）换成中文提示。
        detail: Any = exc.detail
        if exc.status_code == 405:
            message = "请求方法不支持"
        elif not isinstance(detail, str) or not detail or detail in FRAMEWORK_DEFAULT_DETAILS:
            message = DEFAULT_MESSAGES[code]
        else:
            message = detail
        return error_response(code, str(message), request)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 调试细节只写日志（且脱敏），不进响应体；响应固定中文提示
        logger.info("validation_error: %s", mask_sensitive(str(exc.errors())))
        return error_response(422, DEFAULT_MESSAGES[422], request)

    @app.exception_handler(Exception)
    async def unknown_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled request_id=%s path=%s",
            _request_id(request),
            mask_sensitive(request.url.path),
        )
        return error_response(500, DEFAULT_MESSAGES[500], request)
