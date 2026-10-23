"""Alembic environment. Run through bartendro.db.upgrade() (or `bartendro-db upgrade`), which
passes the engine in; there is no alembic.ini.

New migration after changing models.py (from the repo root):
    bartendro-db --db scratch.db revision -m "what changed"

SQLite can't ALTER most things, so migrations rebuild tables (batch mode: copy, drop, rename).
Dropping a table that other tables point at must not trip foreign keys (or cascade-delete
rows), so enforcement is off while migrating - SQLite's documented procedure - and the
foreign keys are checked before committing. All migrations run in one transaction
(make_engine emits a real BEGIN), so a failure leaves the database as it was.
"""

from alembic import context

from bartendro.db.models import Base

config = context.config
engine = config.attributes["engine"]

with engine.connect() as connection:
    raw = connection.connection.driver_connection  # sqlite3 connection, outside any transaction
    raw.execute("PRAGMA foreign_keys=OFF")         # can't be changed inside a transaction
    try:
        # left over by a migration that failed half way under an older version of this file
        for (name,) in raw.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                   "AND name LIKE '\\_alembic\\_tmp\\_%' ESCAPE '\\'").fetchall():
            raw.execute(f'DROP TABLE "{name}"')
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
            problems = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"migration left broken references: {problems[:5]}")
    finally:
        raw.execute("PRAGMA foreign_keys=ON")
