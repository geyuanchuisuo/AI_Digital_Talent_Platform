"""Alembic 环境：连接串取自 app.config settings（backend/.env），不落硬编码。

online 模式直接用 settings.database_url；offline（--sql）用同一 URL 生成 SQL。
本文件不 import app.models（迁移脚本各自 import 所需模型），避免引入加载侧效应。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用运行配置覆盖 alembic.ini 的占位 URL
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
