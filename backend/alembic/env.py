"""Alembic 迁移环境（异步引擎）。"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.audit import AuditLog  # noqa: F401  保证 autogenerate 能看到审计表
from app.core.base import Base
from app.core.config import settings
from app.modules.approval import model as approval_model  # noqa: F401
from app.modules.agent import model as agent_model  # noqa: F401
from app.modules.customer import model as customer_model  # noqa: F401
from app.modules.followup import model as followup_model  # noqa: F401
from app.modules.inquiry import model as inquiry_model  # noqa: F401
from app.modules.file import model as file_model  # noqa: F401
from app.modules.integration import model as integration_model  # noqa: F401
from app.modules.lead import model as lead_model  # noqa: F401
from app.modules.notification import model as notification_model  # noqa: F401
from app.modules.opportunity import model as opportunity_model  # noqa: F401
from app.modules.order import model as order_model  # noqa: F401
from app.modules.payment import model as payment_model  # noqa: F401
from app.modules.pricing import model as pricing_model  # noqa: F401
from app.modules.product import model as product_model  # noqa: F401
from app.modules.quote import model as quote_model  # noqa: F401
from app.modules.sample import model as sample_model  # noqa: F401
from app.modules.settings import model as settings_model  # noqa: F401
from app.modules.task import model as task_model  # noqa: F401
from app.modules.user import model as user_model  # noqa: F401
from app.modules.wecom import model as wecom_model  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
