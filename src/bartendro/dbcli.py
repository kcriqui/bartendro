"""bartendro-db: create, migrate, import and inspect the drink database.

Examples:
    bartendro-db upgrade                      # create / migrate (path from the config file)
    bartendro-db import /path/to/old/bartendro.db
    bartendro-db show                         # dispensers and the drinks you can make
    bartendro-db --db test.db show
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import func, select

from . import __version__
from . import config as config_mod
from .db import current_revision, make_engine, open_db, upgrade
from .db import options
from .db.importer import ImportError_, import_legacy
from .db.menu import makeable_drinks
from .db.models import Dispenser, Drink, Ingredient, PourLog


def _db_path(args) -> Path:
    if args.db:
        return Path(args.db)
    return args.config.database_path()


def cmd_upgrade(args) -> int:
    path = _db_path(args)
    engine = make_engine(path)
    before = current_revision(engine)
    upgrade(engine)
    after = current_revision(engine)
    print(f"{path}: schema {before or 'none'} -> {after}" if before != after
          else f"{path}: schema up to date ({after})")
    return 0


def cmd_import(args) -> int:
    path = _db_path(args)
    Session = open_db(path)
    with Session() as s:
        report = import_legacy(args.old_db, s, replace=args.replace)
    print(f"Imported {args.old_db} into {path}:")
    for what, n in report.counts.items():
        print(f"  {n:5d} {what}")
    if report.warnings:
        print(f"{len(report.warnings)} warning(s):")
        for w in report.warnings:
            print(f"  - {w}")
    return 0


def cmd_show(args) -> int:
    path = _db_path(args)
    Session = open_db(path)
    count = args.dispensers or args.config.hardware.dispensers
    with Session() as s:
        n_ing = s.scalar(select(func.count()).select_from(Ingredient))
        n_drinks = s.scalar(select(func.count()).select_from(Drink))
        n_pours = s.scalar(select(func.count()).select_from(PourLog))
        print(f"{path}: {n_ing} ingredients, {n_drinks} drinks, {n_pours} pours logged")
        print("Dispensers:")
        for d in s.scalars(select(Dispenser).order_by(Dispenser.number)):
            if count is not None and d.number > count:
                break
            what = d.ingredient.name if d.ingredient else "(empty)"
            print(f"  #{d.number:<3} {what:<32} level {d.level.value}")
        drinks = makeable_drinks(s, dispenser_count=count)
        print(f"Can make {len(drinks)} drink(s):")
        for d in drinks:
            print(f"  {d.name}")
    return 0


def cmd_set_password(args) -> int:
    Session = open_db(_db_path(args))
    pw = getpass.getpass("New admin password: ")
    if not pw or pw != getpass.getpass("Again: "):
        print("passwords empty or don't match; not changed")
        return 1
    with Session() as s:
        options.set_password(s, pw)
        s.commit()
    print("admin password set")
    return 0


def cmd_revision(args) -> int:
    """Developer tool: write a new migration from the difference between models.py and the
    schema of a scratch database (upgraded to head first)."""
    from alembic import command

    from .db import _alembic_config

    engine = make_engine(_db_path(args))
    upgrade(engine)
    command.revision(_alembic_config(engine), message=args.message, autogenerate=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bartendro-db", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--config", help="bot config file (default: $BARTENDRO_CONFIG, ./bartendro.toml, "
                                     "/etc/bartendro/bartendro.toml)")
    ap.add_argument("--db", help="database file (default: [database] path from the config)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("upgrade", help="create the database or migrate it to the current schema") \
        .set_defaults(fn=cmd_upgrade)

    s = sub.add_parser("import", help="copy an old (Python 2) bartendro.db into the database")
    s.add_argument("old_db")
    s.add_argument("--replace", action="store_true", help="delete existing drinks/ingredients first")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("show", help="dispensers and the drinks that can be made")
    s.add_argument("--dispensers", type=int, help="only count the first N dispensers")
    s.set_defaults(fn=cmd_show)

    sub.add_parser("set-password", help="set the admin password").set_defaults(fn=cmd_set_password)

    s = sub.add_parser("revision", help="(developers) autogenerate a migration after changing models.py")
    s.add_argument("-m", "--message", required=True)
    s.set_defaults(fn=cmd_revision)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.config = config_mod.load(args.config)
        return args.fn(args)
    except (config_mod.ConfigError, ImportError_, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
