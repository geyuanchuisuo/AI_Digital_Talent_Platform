"""全局配置（T-01）。

实现口径唯一依据：`docs/运行配置.md` v1.3、`docs/架构设计.md` v1.4 §5.6。
键名、默认值、并发语义一律照抄公共文档，代码里不另起一套。

必填且无默认值（.env 或环境变量提供）：
    LLM_BASE_URL / LLM_API_KEY / MYSQL_PASSWORD / JWT_SECRET
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# BASE_DIR 相对定位（架构 §5.6）：不写死绝对路径，任意 cwd 启动都能读到 backend/.env
BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BASE_DIR / ".env"

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # ---- LLM / Embedding ----
    llm_base_url: str
    llm_api_key: str
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 120.0
    llm_concurrency: int = 2
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-v3"
    embedding_query_cache_ttl: int = 300
    embedding_timeout: float = 10.0
    # Embedding 双模式（PRD D22/G05 本地推理交付项）：api=OpenAI 兼容 HTTP（默认）；
    # local=进程内 sentence_transformers 加载客户本地模型目录（bge-large-zh-v1.5 为
    # 1024 维，与 Milvus collection 冻结维度一致）。local 模式需安装 sentence-transformers。
    embedding_mode: str = "api"
    embedding_local_model_path: str = ""    # local 模式：模型目录（客户本地，不进 Git）
    embedding_local_device: str = ""        # local 模式：cpu/cuda（空=cpu）
    vector_sync_timeout: float = 5.0  # H-14（运行配置 v1.4）：原 1.5s，冷连接 embedding 实测 ~1.7s 会误判超时
    prompt_version: str = "v1.0"
    embedding_dim: int = 1024

    # ---- 重排（PRD D22，D 维度 core/rerank_client；双模式见 .env.example）----
    # off（默认）= 不重排，召回序原样返回（行为与未引入前一致）；api = 百炼 gte-rerank
    # 等 HTTP 端点；local = 进程内 sentence_transformers CrossEncoder（客户本地模型目录）。
    # 缺配置/初始化失败明确报错，不静默假装重排过。
    rerank_mode: str = "off"
    rerank_base_url: str | None = None      # api 模式；空回退 Embedding 配置（同供应商体系）
    rerank_api_key: str | None = None       # api 模式；空回退 Embedding 配置
    rerank_model: str = "gte-rerank"        # api 模式模型名
    rerank_timeout: float = 10.0            # api 模式单次超时（同步检索链，不重试）
    rerank_local_model_path: str = ""       # local 模式：bge-reranker 模型目录（客户本地）
    rerank_local_device: str = ""           # local 模式：cpu/cuda/auto（空=auto）

    # ---- MySQL ----
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str
    mysql_db: str = "ai_talent"
    mysql_pool_size: int = 10
    mysql_max_overflow: int = 20
    mysql_echo: bool = False

    # ---- Milvus ----
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "talent_vectors"

    # ---- JWT ----
    jwt_secret: str
    jwt_expire_hours: int = 24

    # ---- 文件上传 ----
    upload_dir: str = "./data/uploads"
    upload_max_mb: int = 20
    upload_allowed_ext: str = "pdf,docx"

    # ---- OCR / PDF 页渲染（REQ-B-20261106-01，默认关闭；B 在 extract_service 适配层消费）----
    # 开启后 upload_allowed_ext 建议扩展为 "pdf,docx,png,jpg,jpeg"（由部署方自行决定）
    ocr_enabled: bool = False
    ocr_model_dir: str = "./data/models/ocr"
    ocr_max_page_pixels: int = 2_000_000  # 单页渲染像素上限（宽*高），超限缩放
    ocr_max_pages: int = 50  # 单文件最大处理页数，超出按页截断并标记 truncated
    ocr_timeout_seconds: float = 120.0  # 单文件 OCR 总超时

    # ---- TaskRunner ----
    task_concurrency: int = 2
    runner_scan_interval: float = 2.0
    task_heartbeat_timeout: int = 600
    task_shutdown_timeout: int = 30

    # ---- Redis 缓存/限流（T-D01，D 维度 core/cache；契约 docs/modules/D/缓存限流契约.md）----
    # 空 REDIS_URL = 未启用：缓存读写与限流全部按「缓存不存在」降级（限流放行/读直查库），
    # 应用行为与无 Redis 时完全一致；配置后由 A 在 main.py 挂载 RateLimitMiddleware 生效。
    redis_url: str = ""
    rate_limit_per_minute: int = 120
    rate_limit_window_seconds: int = 60
    rate_limit_blacklist_ttl: int = 3600
    # D02（Master 统一执行表）：限流环境开关。
    # - False（默认）：演示/验收环境维持 main.py 的 `/api` 全量豁免。原因是黑名单键
    #   `aitalent:ratelimit:blacklist:{ip}` 写在共享 Redis，多环境共用会把其它环境
    #   的业务请求全部打成 403（2026-11-08 用户验收实测两轮）。
    # - True：客户模式，`main.py` 只豁免 `/api/health`，429 / 403 / 降级按
    #   `docs/modules/D/缓存限流契约.md` 生效。**必须**给每个环境配独立 Redis 实例
    #   或独立 DB 号（REDIS_URL 的 `/N`），否则仍会跨环境串扰。
    # 仅控制 A 挂载的豁免路径；D 的 RateLimitMiddleware / CacheClient 实现不改。
    rate_limit_enabled: bool = False

    # ---- PostgreSQL 只读分析副本（T-D02，D 维护 scripts/D/etl_pgsql.py；契约 docs/modules/D/ETL说明.md）----
    # 空 ANALYTICS_DB_URL = 副本未启用：自由问数/报表降级主库 MySQL 直查（V2.6 确认的回退口径）。
    analytics_db_url: str = ""

    # ---- NL2SQL 受控执行（T-D09，D 维度 analytics/nl2sql；契约 docs/modules/D/问数切片契约.md §8）----
    # 超时由 PG statement_timeout 强制；行数由 fetchmany(max+1) 截断——均为 T-D09 ③ 默认口径。
    nl2sql_timeout_seconds: int = 30
    nl2sql_max_rows: int = 1000

    # ---- 应用与网络 ----
    cors_origins: str = "*"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",  # 允许 .env 存在前端键/临时键，避免启动失败
        case_sensitive=False,
    )

    # ---- 派生属性（不新增配置项，只做组合/回退） ----
    @property
    def database_url(self) -> str:
        """SQLAlchemy 同步连接串；用户名/密码做 URL 编码。"""
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+pymysql://{user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def effective_embedding_base_url(self) -> str:
        """运行配置 v1.3：EMBEDDING_BASE_URL 为空时回退到 LLM_BASE_URL。"""
        return self.embedding_base_url or self.llm_base_url

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    @property
    def cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def upload_allowed_ext_list(self) -> list[str]:
        return [item.strip().lower().lstrip(".") for item in self.upload_allowed_ext.split(",") if item.strip()]

    @property
    def upload_dir_path(self) -> Path:
        path = Path(self.upload_dir)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    def load_runtime_overrides(self) -> None:
        """唯一从 DB 覆盖的配置项：task_concurrency（运行配置 v1.3「读取优先级」）。

        T-03 建 sys_settings 表之前 dao.sys_settings 尚不存在，此时保留 .env 兜底值；
        表就绪后自动以页面设置为准（无需重启即生效，重启后保持）。
        """
        try:
            from app.dao.sys_settings import get_value  # type: ignore[import-not-found]

            value = get_value("task_concurrency")
            if value is not None:
                self.task_concurrency = int(value)
        except ImportError:
            logger.debug("sys_settings 尚未实现（T-03），task_concurrency 用 .env 兜底值 %s", self.task_concurrency)
        except Exception:  # noqa: BLE001 - 读表失败不能阻塞启动
            logger.warning("读取 sys_settings.task_concurrency 失败，保留兜底值", exc_info=True)


settings = Settings()
