"""成功 / 202 统一返回（T-02；`docs/接口设计.md` v1.3 §1.1）。

```json
{ "code": 0, "message": "success", "data": { } }
```
列表类 data：`{"items": [...], "total": 100, "page": 1, "page_size": 20}`。

设计取舍（已登记开发日志 Layer 3 T-02）：**不做全局响应包装中间件**，
由业务接口显式调用 `ok()` / `paginated()`。原因是 `/api/health` 是唯一例外、
文件/图片下载走 blob 不走统一 code 判断（错误码文档 §七），自动包装会误伤这两类。
"""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

SUCCESS_MESSAGE = "success"


def ok(
    data: Any = None,
    *,
    status_code: int = 200,
    message: str = SUCCESS_MESSAGE,
) -> JSONResponse:
    """统一成功响应；批次创建类接口传 `status_code=202`。

    半写场景：MySQL 已提交但向量失败时，用
    `ok(data_with_vector_status_failed, message=HALF_WRITE_VECTOR_MESSAGE)` 返回 200，
    **不要抛 BizError(503)**。
    """
    if status_code not in (200, 202):
        raise ValueError(f"统一成功响应只允许 200 / 202，收到 {status_code}")
    return JSONResponse(
        status_code=status_code,
        content={
            "code": 0,
            "message": message,
            "data": jsonable_encoder(data),
        },
    )


def paginated(
    items: Any,
    total: int,
    page: int,
    page_size: int,
    *,
    message: str = SUCCESS_MESSAGE,
) -> JSONResponse:
    """列表类统一返回；分页参数由 `app.api.deps.pagination` 提供（上限 200）。"""
    return ok(
        {"items": items, "total": total, "page": page, "page_size": page_size},
        message=message,
    )
