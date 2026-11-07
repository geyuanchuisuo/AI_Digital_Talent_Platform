"""L1 单元测试 · T-02 统一返回与异常体系（独立 app，全部不触外部服务）。

覆盖：成功/202/列表统一体、八个错误码白名单、四类异常处理器、
request_id 仅错误携带、分页上限 200、脱敏日志、**半写场景不返回 503**。
"""

from __future__ import annotations

import logging

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.deps import PageParams, pagination
from app.core.errors import (
    ALLOWED_ERROR_CODES,
    HALF_WRITE_VECTOR_MESSAGE,
    BizError,
    register_exception_handlers,
)
from app.core.response import ok, paginated


class Payload(BaseModel):
    phone: str
    age: int


@pytest.fixture
def client():
    """独立 app：只挂 T-02 的处理器与固定 request_id 中间件，隔离生产路由。"""
    app = FastAPI()

    @app.middleware("http")
    async def add_request_id(request, call_next):
        request.state.request_id = "req-test-fixed"
        return await call_next(request)

    register_exception_handlers(app)

    @app.get("/ok")
    def route_ok():
        return ok({"a": 1})

    @app.get("/accepted")
    def route_accepted():
        return ok({"task_id": 12}, status_code=202)

    @app.get("/list")
    def route_list(p: PageParams = Depends(pagination)):
        return paginated([], 0, p.page, p.page_size)

    @app.get("/biz-400")
    def route_biz_400():
        raise BizError(400, "weights 总和必须为 100")

    @app.get("/biz-503")
    def route_biz_503():
        # 请求尚未产生任何业务写入 → 允许 503
        raise BizError(503, "语义检索服务暂时不可用，请稍后重试")

    @app.get("/http-401")
    def route_http_401():
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    @app.get("/http-404")
    def route_http_404():
        raise HTTPException(status_code=404, detail="记录不存在")

    @app.get("/boom")
    def route_boom():
        raise RuntimeError("boom-secret-should-not-leak")

    @app.post("/validate")
    def route_validate(payload: Payload):
        return ok(payload)

    @app.post("/half-write")
    def route_half_write():
        # 模拟 MySQL 事务已提交后 embedding/Milvus 失败（决策 D-B）
        return ok({"id": 1, "vector_status": "failed"}, message=HALF_WRITE_VECTOR_MESSAGE)

    return TestClient(app, raise_server_exceptions=False)


def _assert_error(body: dict, code: int, message: str):
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == code
    assert body["message"] == message
    assert body["data"] is None
    assert body["request_id"] == "req-test-fixed"
    assert "detail" not in body


# ---- 成功 / 202 / 列表 ----

def test_success_body_and_no_request_id(client):
    resp = client.get("/ok")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"code": 0, "message": "success", "data": {"a": 1}}
    assert "request_id" not in body


def test_202_body(client):
    resp = client.get("/accepted")
    assert resp.status_code == 202
    assert resp.json() == {"code": 0, "message": "success", "data": {"task_id": 12}}


def test_paginated_defaults_and_offset(client):
    body = client.get("/list").json()
    assert body["data"] == {"items": [], "total": 0, "page": 1, "page_size": 20}
    assert PageParams(page=3, page_size=20).offset == 40
    assert PageParams(page=1, page_size=200).limit == 200


def test_page_size_upper_bound_returns_unified_422(client):
    resp = client.get("/list?page_size=201")
    assert resp.status_code == 422
    _assert_error(resp.json(), 422, "请求参数结构不合法")


def test_page_lower_bound(client):
    resp = client.get("/list?page=0")
    assert resp.status_code == 422
    _assert_error(resp.json(), 422, "请求参数结构不合法")


# ---- 四类异常 ----

def test_biz_error_400(client):
    resp = client.get("/biz-400")
    assert resp.status_code == 400
    _assert_error(resp.json(), 400, "weights 总和必须为 100")
    assert resp.headers["X-Request-ID"] == "req-test-fixed"


def test_biz_error_503_only_before_any_write(client):
    resp = client.get("/biz-503")
    assert resp.status_code == 503
    _assert_error(resp.json(), 503, "语义检索服务暂时不可用，请稍后重试")


def test_http_exception_keeps_chinese_detail(client):
    _assert_error(client.get("/http-401").json(), 401, "未登录或登录已过期")
    _assert_error(client.get("/http-404").json(), 404, "记录不存在")


def test_route_not_found_is_unified(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    # 框架默认英文 detail 换成中文提示（文档 §一：message 为中文业务提示）
    _assert_error(resp.json(), 404, "记录不存在")


def test_method_not_allowed_normalized_without_new_code(client):
    """405 不在八个错误码内 → 归一为 400，中文提示，不引入细分码。"""
    resp = client.post("/ok")
    assert resp.status_code == 400
    _assert_error(resp.json(), 400, "请求方法不支持")


def test_unhandled_exception_is_500_without_leaking(client):
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    _assert_error(body, 500, "服务器内部错误")
    assert "boom-secret" not in str(body)
    # 未捕获异常由最外层处理器返回，仍应带排障头
    assert resp.headers["X-Request-ID"] == "req-test-fixed"


def test_validation_error_unified_and_log_masked(client, caplog):
    # set_level（而非 at_level）：同时设置捕获 handler 级别，避免全量运行中
    # 其他测试改动 logger 状态后本用例间歇性捕获不到（2026-11-07 修复 flaky）
    caplog.set_level(logging.INFO, logger="app.error")
    resp = client.post("/validate", json={"phone": "13800000000"})
    assert resp.status_code == 422
    _assert_error(resp.json(), 422, "请求参数结构不合法")
    assert "138****0000" in caplog.text
    assert "13800000000" not in caplog.text


# ---- 半写口径（决策 D-B）----

def test_half_write_never_returns_503(client):
    resp = client.post("/half-write")
    assert resp.status_code == 200, "MySQL 已提交后向量失败必须是 200，不得 503"
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["vector_status"] == "failed"
    assert body["message"] == HALF_WRITE_VECTOR_MESSAGE
    assert "request_id" not in body
    assert resp.status_code != 503


# ---- 白名单与契约 ----

def test_error_code_whitelist():
    assert ALLOWED_ERROR_CODES == {400, 401, 403, 404, 409, 422, 500, 503}
    for code in ALLOWED_ERROR_CODES:
        assert BizError(code, "x").status_code == code
    with pytest.raises(ValueError):
        BizError(1001, "细分码不被允许")
    with pytest.raises(ValueError):
        BizError(418, "非白名单 HTTP 码")


def test_ok_rejects_non_200_202():
    with pytest.raises(ValueError):
        ok({}, status_code=201)
