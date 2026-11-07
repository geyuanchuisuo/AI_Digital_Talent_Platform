"""Alembic env: DB URL comes from app.config settings (backend/.env), never hardcoded.

Online mode uses settings.database_url directly; offline (--sql) generates SQL from
the same URL. This file does NOT import app.models (each migration imports what it
needs) to avoid import side effects.

NOTE: keep migration scripts ASCII-only. Alembic 1.20 reads ini/scripts with the
system locale encoding on Windows (GBK); Chinese text here breaks startup on
Windows hosts. Chinese docs live in the migration-chain doc under docs/modules/A/.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the placeholder URL in alembic.ini with the runtime settings
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
