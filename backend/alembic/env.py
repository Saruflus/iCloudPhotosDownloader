"""Alembic environment.

Migrations run synchronously (psycopg). The DB URL is taken from app settings
(D11), not from alembic.ini, so there is one source of truth.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.core.database import Base

# Import models so they register on Base.metadata for autogenerate.
import app.models.assets  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Take the URL straight from settings. We deliberately do NOT push it into
# alembic.ini via config.set_main_option(): configparser's %-interpolation
# corrupts/raises on passwords containing '%'.
DB_URL = get_settings().sync_database_url

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a live DB connection (``alembic ... --sql``)."""
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DB_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
