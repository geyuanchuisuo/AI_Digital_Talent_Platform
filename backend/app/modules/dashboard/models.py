"""AI 中台管理面模型（T-D20，D_0011 迁移，80 表基线）。

⚠️ 与 workflows models 同一纪律：定义 ORM 类、被 app/models/__init__.py 导入，
**不要作为 import 入口**；使用方一律 `from app.models import AiProvider`。

结构（契约 docs/modules/D/AI中台契约.md）：
- AiProvider          模型供应商（多供应商接入；启停即时生效；密钥仅落库不回显）
- AiPromptVersion     Prompt 版本库（name+version 唯一；draft/published/deprecated；发布后不可变）
- AiCallLog           LLM 调用日志（llm_client 埋点逐次落库；用量/错误率看板的数据源）

口径：
- 密钥仅本地安全配置/库内存储，任何接口响应与日志**不回显**（验收①断言）；
- 供应商启停即时生效：llm_client 执行面读 enabled（验收②）；
- 调用日志逐次落库，看板数字可回查明细（验收③）；
- 智能体一期以受控工作流定义为载体（需求明确不另造第三套编排），注册/发布/
  退役复用 T-D15 生命周期，运行看板 = workflow_runs 聚合，本模块只提供只读视图。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    JSONDict,
    MYSQL_TABLE_OPTIONS,
    PKMixin,
    UpdatedAtMixin,
)

# 供应商协议类型（一期 OpenAI 兼容为主；扩展走新枚举值，不改表）
AI_PROVIDER_TYPES: tuple[str, ...] = ("openai_compatible",)

# Prompt 版本状态机（与 Skills/流程版本同构；发布后不可变）
AI_PROMPT_STATUSES: tuple[str, ...] = ("draft", "published", "deprecated")

# 调用结果
AI_CALL_RESULTS: tuple[str, ...] = ("ok", "error")


class AiProvider(PKMixin, UpdatedAtMixin, Base):
    """模型供应商（`ai_providers`）。

    api_key 仅落库（本地安全配置读取写入），序列化层永不回显（T-D20 验收①）；
    enabled=False 即停用，执行面读取时即时生效（验收②）；同时刻仅一个
    enabled 供应商作为默认执行面（is_default 唯一性由 service 层 CAS 保证）。
    """

    __tablename__ = "ai_providers"
    __table_args__ = (
        UniqueConstraint("name", name="uk_ai_providers_name"),
        MYSQL_TABLE_OPTIONS,
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    enabled: Mapped[int] = mapped_column(nullable=False, default=1)  # 1/0（MySQL tinyint 口径）
    is_default: Mapped[int] = mapped_column(nullable=False, default=0)
    timeout_seconds: Mapped[int] = mapped_column(nullable=False, default=120)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(nullable=True)


class AiPromptVersion(PKMixin, UpdatedAtMixin, Base):
    """Prompt 版本（`ai_prompt_versions`）。

    与 core/prompts 文件加载器（B/A 业务提示词）并存：本表存中台可配置的
    D 域/通用 Prompt；发布后内容不可变（版本锁定，验收与 Skills 同口径）。
    """

    __tablename__ = "ai_prompt_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uk_ai_prompt_versions"),
        Index("idx_aip_name_status", "name", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    content: Mapped[str] = mapped_column(String(8000), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(nullable=True)
    published_by: Mapped[int | None] = mapped_column(nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AiCallLog(PKMixin, UpdatedAtMixin, Base):
    """LLM 调用日志（`ai_call_logs`）：llm_client 逐次调用落一行。

    purpose 记业务用途（如 parse/match/assessment_grading/nl2sql）；
    latency_ms/tokens 供用量看板；error 仅失败行非空（截断 500）。
    """

    __tablename__ = "ai_call_logs"
    __table_args__ = (
        Index("idx_aic_created", "created_at"),
        Index("idx_aic_purpose", "purpose"),
        MYSQL_TABLE_OPTIONS,
    )

    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="unspecified")
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    latency_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(nullable=True)
