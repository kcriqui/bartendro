"""Alembic environment. Run through bartendro.db.upgrade() (or `bartendro-db upgrade`), which
passes the engine in; there is no alembic.ini.

New migration after changing models.py (from the repo root):
    bartendro-db --db scratch.db revision -m "what changed"
"""

from alembic import context

from bartendro.db.models import Base

config = context.config
engine = config.attributes["engine"]

with engine.begin() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata,
                      render_as_batch=True)  # SQLite can't ALTER most things: copy-and-move
    with context.begin_transaction():
        context.run_migrations()
