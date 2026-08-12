"""Alembic's entry point.

The database URL comes from `config/settings.py`, never from alembic.ini. One
source for it means a migration cannot run against a different database than the
application uses -- which is the failure mode where a migration "succeeds" and
the app still reports a missing column.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from api.db.base import Base

# Imported for the side effect: a model class must be imported before Base
# knows its table, and autogenerate would otherwise emit a migration dropping
# every table it "cannot see".
from api.db import models  # noqa: F401
from config.settings import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.API_DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it -- `alembic upgrade head --sql`."""
    context.configure(
        url=settings.API_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Off by default, and worth having on: a column whose type changed
            # in models.py is otherwise not noticed by autogenerate at all.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
