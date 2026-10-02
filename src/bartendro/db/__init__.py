"""SQLite database: open it, bring its schema up to date with Alembic, make sessions."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS = Path(__file__).parent / "migrations"
_generation = 0


@event.listens_for(Session, "after_commit")
def _committed(_session) -> None:
    global _generation
    _generation += 1


def generation() -> int:
    """Goes up on every commit in this process, in any session: whatever was worked out from
    the database (the web app's menu) is stale once it changes."""
    return _generation


def make_engine(path: str | Path) -> Engine:
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # Let SQLAlchemy run the transactions: Python's sqlite3 module otherwise skips BEGIN
        # before DDL, so a failed migration would leave a half-changed schema.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine


def _alembic_config(engine: Engine):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["engine"] = engine
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    """Create the tables, or migrate an existing database to the current schema."""
    from alembic import command

    if "ingredient" not in inspect(engine).get_table_names() and \
            "booze" in inspect(engine).get_table_names():
        raise RuntimeError("this is an old (Python 2) bartendro.db - use `bartendro-db import` "
                           "to copy it into a new database instead")
    command.upgrade(_alembic_config(engine), revision)


def current_revision(engine: Engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def open_db(path: str | Path, migrate: bool = True) -> sessionmaker[Session]:
    """Open (creating if needed) the database at `path` and return a session factory."""
    engine = make_engine(path)
    if migrate:
        upgrade(engine)
    return sessionmaker(engine)
