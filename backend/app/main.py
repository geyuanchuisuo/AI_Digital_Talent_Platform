"""FastAPI 应用入口（T-01 骨架 + T-02 统一返回与异常体系 + T-05 Milvus 启动自检 + T-12 TaskRunner）。

职责：CORS / 全局异常处理器 / 请求日志·脱敏·request_id /
lifespan（Milvus collection 自检 + TaskRunner 启停）/ 路由注册。

- T-02 起通过 `app.core.errors.register_exception_handlers(app)` 注册四类全局处理器；
  业务接口用 `app.core.response.ok()` / `paginated()` 返回统一成功体。
- `GET /api/health` 是统一返回体的唯一例外（接口 §3），已在 api/health.py 直接返回结构。
- T-05 起 lifespan 启动时调用 `milvus_store.ensure_collection()`：collection 不存在按
  `EMBEDDING_DIM` 创建、维度不符只记 error 日志后继续启动（Milvus 不可用不得阻塞应用，
  降级口径见架构 §5.3；真正写入时由 milvus_store 抛错并落 `vector_status=failed`）。
- 一期必须 `--workers 1`（接口 §8 单进程约束，TaskRunner 常驻单例）。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth as auth_api
from app.api import health as health_api
from app.api import job_profiles as job_profiles_api
from app.api import match as match_api
from app.api import screen as screen_api
from app.api import resumes as resumes_api
from app.api import talents as talents_api
from app.config import settings
from app.core.cache import RateLimitMiddleware
from app.core.errors import register_exception_handlers
from app.utils.ids import new_request_id
from app.utils.mask import mask_sensitive

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.request")


def _ensure_milvus_collection() -> None:
    """T-05 启动自检：collection 不存在按配置维度创建；异常不阻塞启动（降级由 health 体现）。"""
    try:
        from app.core.milvus_store import get_store

        get_store().ensure_collection()
        logger.info("startup: Milvus collection=%s 自检通过", settings.milvus_collection)
    except Exception as exc:  # noqa: BLE001 - 向量依赖不可用时应用仍须可启动
        logger.error("startup: Milvus collection 自检未通过（向量功能降级）：%s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子：Milvus 自检 + TaskRunner 单例启停（架构 §5.4）。

    T-12：runner 启动即做无条件 running→pending 恢复（保留 stage），随后 2s 一轮扫描。
    T-13 起在这里注册解析执行体；T-24/T-25 同理挂 screen/match。
    **没有注册 handler 的任务类型不会被认领**，所以"注册"就是"开启消费"的开关。
    """
    from app.tasks.parse_worker import handle_parse
    from app.tasks.runner import runner
    from app.tasks.match_worker import handle_match_batch
    from app.tasks.screen_worker import handle_screen_batch

    settings.load_runtime_overrides()
    _ensure_milvus_collection()
    runner.register("parse", handle_parse)
    runner.register("screen", handle_screen_batch)
    runner.register("match", handle_match_batch)
    runner.start()
    logger.info(
        "startup: env=%s task_concurrency=%s worker=%s",
        settings.app_env,
        settings.task_concurrency,
        "running" if runner.is_running else "stopped",
    )
    try:
        yield
    finally:
        runner.stop()
        logger.info("shutdown: worker=stopped")


app = FastAPI(
    title="AI 数字化人才平台",
    version=health_api.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=settings.cors_origins_list != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# T-D01（REQ-D-20261107-183200，2026-11-07 用户指令 D 直接落地）：Redis 限流/黑名单中间件。
# REDIS_URL 为空 = 直通零副作用；配置后固定窗口限流 + 黑名单 403，Redis 停服降级放行。
# 契约：docs/modules/D/缓存限流契约.md。
app.add_middleware(RateLimitMiddleware)

# T-02：四类全局异常处理器（BizError / HTTPException / RequestValidationError / Exception）
# 口径见 docs/统一错误码与鉴权.md v1.2 §六；request_id 由下方中间件写入 request.state。
register_exception_handlers(app)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """生成 request_id + 请求日志；日志中的手机号/邮箱脱敏（架构 §5.1）。"""
    request_id = new_request_id()
    request.state.request_id = request_id
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        # 响应头便于排障；响应体仍不带 request_id（成功/202 不带，仅错误体带）
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        logger.exception("unhandled request_id=%s", request_id)
        raise
    finally:
        path = request.url.path
        if request.url.query:
            path += "?" + request.url.query
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s status=%s %.1fms user=- request_id=%s",
            request.method,
            mask_sensitive(path),
            status,
            elapsed_ms,
            request_id,
        )


app.include_router(health_api.router)
app.include_router(auth_api.router)
app.include_router(resumes_api.router)
app.include_router(job_profiles_api.router)
app.include_router(screen_api.router)
app.include_router(match_api.router)
# ⚠️ talents 的 `/duplicates` 必须排在将来 T-18 的 `/{talent_id}` 之前（api/talents.py 内已注明）
app.include_router(talents_api.router)

# C 模块三域路由（测评 / 人工面试 / 培训）：由 C 的 registry 单一来源提供（C 请求 §5.2）
from app.modules.assessments.registry import ALL_ROUTERS as _C_ROUTERS  # noqa: E402

for _c_router in _C_ROUTERS:
    app.include_router(_c_router)

# M3 招聘域（人才需求/项目、JD 投放、申请五阶段状态机）：A 的 modules/recruitment（V2.6 冻结口径）
from app.modules.recruitment.router import router as recruitment_router  # noqa: E402

app.include_router(recruitment_router)

# D 通知中心（T-D03，REQ-D-20261107-183200 第 5 项，2026-11-07 用户指令直接落地）：
# /api/notifications 列表/详情/已读，鉴权走公共 deps（CurrentUser/get_db）
from app.modules.notifications.router import router as notifications_router  # noqa: E402

app.include_router(notifications_router)
