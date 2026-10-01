"""SQLite database: open it, bring its schema up to date with Alembic, make sessions."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS = Path(__file__).parent / "migrations"


def make_engine(path: str | Path) -> Engine:
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

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
