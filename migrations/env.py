"""SQLite migrations: shared settings, explicit DDL transaction, batch ALTER."""
from alembic import context

from app.orm_models import Base
from app.schema_migrations import migration_connection


if context.is_offline_mode():
    raise ValueError("SQLite migrations require an online connection to validate the existing schema")

try:
    # upgrade/downgrade/stamp supply a destination; inspection/autogenerate do not.
    context.get_revision_argument()
    writable = True
except KeyError:
    writable = False

with migration_connection(
    context.config, context.get_x_argument(as_dictionary=True), writable=writable,
) as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
        transactional_ddl=True,
        dont_mutate=not writable,
    )
    with context.begin_transaction():
        context.run_migrations()
