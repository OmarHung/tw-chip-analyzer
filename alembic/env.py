"""Alembic 環境。使用專案 settings 與 Base.metadata（同步 psycopg 引擎）。"""
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

from app.core.config import get_settings
from app.db.base import Base
import app.db.models  # noqa: F401  匯入以註冊所有 table 到 metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    # migration 使用同步連線（psycopg3 同時支援 sync/async）。
    return get_settings().active_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_sync_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
