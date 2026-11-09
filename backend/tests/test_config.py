"""L1 单元测试 · config.py（运行配置 v1.3 口径）。

不触真实外部服务；验证四个必填键、默认值、派生属性、env 覆盖优先级。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import BASE_DIR, Settings, settings

REQUIRED_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "MYSQL_PASSWORD", "JWT_SECRET")


def test_four_required_keys_present():
    """todolist T-01 验收点：config 加载不报 ValidationError。"""
    assert settings.llm_base_url
    assert settings.llm_api_key
    assert settings.mysql_password
    assert settings.jwt_secret


def test_defaults_match_runtime_config_v13(monkeypatch):
    # Defaults must be measured without the developer's .env or process overrides.
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    cfg = Settings(_env_file=None, llm_base_url="http://127.0.0.1:9/v1",
                   llm_api_key="unit-test", mysql_password="unit-test", jwt_secret="unit-test")
    assert cfg.llm_timeout == 120.0
    assert cfg.llm_concurrency == 2
    assert cfg.embedding_model == "text-embedding-v3"
    assert cfg.embedding_timeout == 10.0
    # H-14 / 裁决 C-01（2026-11-07）：冷连接 ~1.7s 会被 1.5 误判超时，运行时已改 5.0
    assert cfg.vector_sync_timeout == 5.0
    assert cfg.prompt_version == "v1.0"
    assert cfg.mysql_db == "ai_talent"
    assert cfg.milvus_collection == "talent_vectors"
    assert cfg.jwt_expire_hours == 24
    assert cfg.upload_max_mb == 20
    assert cfg.task_concurrency == 2
    assert cfg.runner_scan_interval == 2.0
    assert cfg.task_heartbeat_timeout == 600
    assert cfg.task_shutdown_timeout == 30
    assert cfg.embedding_dim == 1024


def test_derived_properties():
    assert settings.database_url.startswith("mysql+pymysql://")
    assert settings.mysql_db in settings.database_url
    assert settings.effective_embedding_base_url == (settings.embedding_base_url or settings.llm_base_url)
    assert settings.effective_embedding_api_key
    assert settings.cors_origins_list == ["*"] or all(isinstance(x, str) for x in settings.cors_origins_list)
    assert settings.upload_allowed_ext_list == ["pdf", "docx"]
    assert settings.upload_dir_path.is_absolute()
    assert BASE_DIR.name == "backend"


def test_embedding_fallback_to_llm(monkeypatch):
    """环境变量置空 → 运行配置 v1.3 的回退口径生效。"""
    monkeypatch.setenv("EMBEDDING_BASE_URL", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    cfg = Settings()  # 默认读 .env 拿四个必填键，env 中的空值覆盖这两项
    assert cfg.effective_embedding_base_url == cfg.llm_base_url
    assert cfg.effective_embedding_api_key == cfg.llm_api_key


def test_env_overrides_default(monkeypatch):
    """读取优先级：环境变量 > .env > config.py 硬编码默认值。"""
    monkeypatch.setenv("TASK_HEARTBEAT_TIMEOUT", "900")
    assert Settings().task_heartbeat_timeout == 900


def test_missing_required_key_raises_validation_error(monkeypatch):
    for key in REQUIRED_KEYS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_load_runtime_overrides_is_idempotent():
    """T-03 起 dao.sys_settings 已存在；读表失败或值未变时静默保留兜底值，不抛异常。

    覆盖「表值真正生效」的正向断言在 tests/test_db_integration_t03.py（L2，用独立
    Settings 实例改值后再读，避免污染全局 settings）。
    """
    before = settings.task_concurrency
    settings.load_runtime_overrides()
    assert settings.task_concurrency == before
