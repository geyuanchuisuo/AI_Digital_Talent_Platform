"""GET /api/health —— 系统健康检查（`docs/接口设计.md` v1.3 §3）。

约定：
- 免鉴权、快速返回、**不主动调用 LLM / Embedding**（只查配置存在性 + 被动探活缓存）。
- 唯一不套 `{code,message,data}` 的接口，直接返回健康检查结构。
- 任一依赖异常 → `status:"degraded"`，**HTTP 仍 200**。
- B1 阶段取舍（已在开发日志 Layer 3 T-01 登记）：
  * `db` 表示 MySQL 服务连通性（与 check_env.py 同语义，不指定库），`ai_talent` 库由 T-03 创建；
    T-03 之后由 T-03 决定是否把「目标库存在性」纳入 db 判定。
  * `milvus` 表示服务连通性（连接 + 取版本）；T-05 起**collection 已存在时额外校验向量维度**，
    维度与 `EMBEDDING_DIM` 冲突则判 false → `status:"degraded"`（字段集合不变）。
    collection 尚未创建（首次写入前）不算故障——lifespan 启动时会按配置维度建好。
  * `worker` 在 T-12 TaskRunner 落地前恒为未启动，**不计入降级判定**（属预期，不是故障）。
"""

from __future__ import annotations

import logging
import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter

from app.config import settings
from app.core import worker_state

router = APIRouter(prefix="/api", tags=["system"])
logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"
_PROBE_TIMEOUT = 2.0  # 秒；health 必须快速返回

# ---- 进程内被动状态（T-05 / T-12 通过下面两个 setter 写入，health 只读） ----
_embedding_alive: bool | None = None  # None = 从未真实调用过 Embedding（接口 §3）


def set_embedding_alive(value: bool) -> None:
    """T-05 embedding_client 每次真实调用后写入结果（被动探活，不主动探测）。"""
    global _embedding_alive
    _embedding_alive = value


def get_embedding_alive() -> bool | None:
    return _embedding_alive


def set_worker_state(*, alive: bool, last_heartbeat: str | None = None, backlog: dict[str, int] | None = None) -> None:
    """T-12 TaskRunner 上报状态（状态本体在 `app/core/worker_state.py`，此处只转调）。"""
    worker_state.set_worker_state(alive=alive, last_heartbeat=last_heartbeat, backlog=backlog)


def _port_open(host: str, port: int, timeout: float = _PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_db() -> bool:
    """轻量连通性：连 MySQL 服务（不指定库）+ SELECT 1。"""
    try:
        import pymysql
    except ImportError:
        return False

    if not _port_open(settings.mysql_host, settings.mysql_port):
        return False
    try:
        conn = pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            connect_timeout=_PROBE_TIMEOUT,
            read_timeout=_PROBE_TIMEOUT,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 - health 永不因探测失败而 500
        logger.warning("health db probe failed: %s", exc)
        return False


def _probe_milvus() -> tuple[bool, str | None]:
    """轻量连通性：连接 + 取服务端版本；collection 已存在时校验维度（不建 collection）。"""
    parsed = urlparse(settings.milvus_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 19530)
    if not _port_open(host, port):
        return False, None
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=settings.milvus_uri)
        try:
            version = str(client.get_server_version())
            if not _collection_dim_ok(client):
                return False, version
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        return True, version
    except Exception as exc:  # noqa: BLE001
        logger.warning("health milvus probe failed: %s", exc)
        return False, None


def _collection_dim_ok(client: Any) -> bool:
    """T-05：collection 存在时校验 `vector` 维度与 `EMBEDDING_DIM` 是否一致。"""
    try:
        if not client.has_collection(settings.milvus_collection):
            return True
        description = client.describe_collection(collection_name=settings.milvus_collection)
        fields = {field.get("name"): field for field in (description or {}).get("fields", [])}
        actual = int((((fields.get("vector") or {}).get("params")) or {}).get("dim") or 0)
        if actual and actual != settings.embedding_dim:
            logger.warning(
                "health: collection %s 维度 %s ≠ EMBEDDING_DIM %s，向量功能不可用",
                settings.milvus_collection,
                actual,
                settings.embedding_dim,
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - 结构检查失败不影响连通性判定
        logger.debug("health collection 结构检查跳过：%s", exc)
        return True


@router.get("/health")
def health() -> dict[str, Any]:
    """接口 §3 的固定字段集合，不新增字段、不套统一返回体。"""
    db_ok = _probe_db()
    milvus_ok, milvus_version = _probe_milvus()
    llm_configured = bool(settings.llm_base_url and settings.llm_api_key)
    embedding_configured = bool(
        settings.effective_embedding_base_url and settings.effective_embedding_api_key
    )
    embedding_alive = get_embedding_alive()

    # embedding_alive 为 None 属「尚未调用」，不降级；明确 False 才降级（架构 §5.3）
    deps_ok = db_ok and milvus_ok and llm_configured and embedding_configured and embedding_alive is not False

    worker = worker_state.get_worker_state()
    # 接口 §3：concurrency 取当前运行时值（T-03 起由 sys_settings 覆盖 .env），不取 import 时快照
    worker["concurrency"] = settings.task_concurrency
    return {
        "status": "ok" if deps_ok else "degraded",
        "db": db_ok,
        "milvus": milvus_ok,
        "milvus_version": milvus_version,
        "llm_configured": llm_configured,
        "embedding_configured": embedding_configured,
        "embedding_alive": embedding_alive,
        "worker": worker,
        "version": APP_VERSION,
    }
