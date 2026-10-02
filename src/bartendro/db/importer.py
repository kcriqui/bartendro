"""Copy an old (Python 2 app) bartendro.db into a new database.

The old file is opened read-only and never changed. Row ids are kept, so a drink or bottle has
the same id before and after. Mapping:

    booze (+ booze_group / booze_group_booze)  -> ingredient (generic_id from the group)
    drink + drink_name, drink_booze            -> drink, recipe_item (parts)
    dispenser                                  -> dispenser (id N -> dispenser #N)
    option                                     -> option (password stored hashed)
    drink_log + shot_log                       -> pour_log
    custom_drink*, wanted_drink_list, version  -> not imported (old UI features / Alembic now)

Old databases from different releases lack some columns or tables; missing ones fall back to
the old app's defaults. Anything skipped or changed is listed in the report's warnings.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from . import options
from .models import Dispenser, Drink, Ingredient, Kind, Level, Option, PourLog, RecipeItem

# old booze.type -> (kind, manual); type 1 "Alcohol" (and any ABV) makes it alcoholic
BOOZE_TYPES = {
    0: (Kind.OTHER, False),    # Unknown
    1: (Kind.OTHER, False),    # Alcohol
    2: (Kind.TART, False),     # Tart
    3: (Kind.SWEET, False),    # Sweet
    4: (Kind.OTHER, True),     # External: added by hand, never pumped
}
OLD_LEVELS = {0: Level.OUT, 1: Level.OK, 2: Level.LOW}  # mixer.LL_OUT / LL_OK / LL_LOW
NOT_IMPORTED = ("custom_drink", "custom_drink_booze", "wanted_drink_list")


class ImportError_(Exception):  # noqa: N801 - don't shadow the builtin ImportError
    pass


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


class _Old:
    """Read-only view of the old database that tolerates missing tables/columns."""

    def __init__(self, path: Path):
        if not path.exists():
            raise ImportError_(f"{path} does not exist")
        # Not a "file:...?mode=ro" URI: SQLite rejects those for Windows network paths
        # (\\server\share\...). query_only keeps the old file untouched just the same.
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA query_only = ON")
        self.conn.row_factory = sqlite3.Row
        try:
            self.tables = {r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as e:
            raise ImportError_(f"{path} is not a SQLite database: {e}") from e
        if "booze" not in self.tables or "drink" not in self.tables:
            raise ImportError_(f"{path} doesn't look like an old bartendro.db (no booze/drink tables)")

    def columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')}

    def rows(self, table: str, order: str = "id") -> list[sqlite3.Row]:
        if table not in self.tables:
            return []
        return self.conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()

    def close(self) -> None:
        self.conn.close()


def _get(row: sqlite3.Row, col: str, default=None):
    return row[col] if col in row.keys() and row[col] is not None else default


def _bool(value, default: bool) -> bool:
    """Old rows hold 0/1, True/False or (from a bad column default) the strings 'f'/'t'."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "t", "true", "y", "yes", "on")
    return bool(value)


def _text(value) -> str:
    return (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value))


def import_legacy(old_path: str | Path, session: Session, replace: bool = False,
                  logs: bool = True, settings: bool = True) -> Report:
    """Import `old_path` into `session`'s database (schema already up to date) and commit.
    Refuses to touch a database that already has drinks or ingredients unless `replace`.
    `logs=False`: leave out the drink / shot logs; `settings=False`: leave out the options
    (sizes, password, ...) - just the recipes and the bottles."""
    old = _Old(Path(old_path))
    try:
        has_data = session.scalar(select(func.count()).select_from(Ingredient)) or \
            session.scalar(select(func.count()).select_from(Drink))
        if has_data and not replace:
            raise ImportError_("the new database already has drinks/ingredients "
                               "(use --replace to delete them first)")
        if replace:
            for model in (PourLog, RecipeItem, Dispenser, Drink, Option):
                session.execute(delete(model))
            session.execute(update(Ingredient).values(generic_id=None))
            session.execute(delete(Ingredient))

        report = Report()
        old_options = {_text(r["key"]): r["value"] for r in old.rows("option", order="rowid")}
        sensors = options.parse("use_liquid_level_sensors",
                                 _text(old_options.get("use_liquid_level_sensors", "0")))

        _import_ingredients(old, session, report)
        _import_drinks(old, session, report)
        _import_dispensers(old, session, report, sensors)
        if settings:
            _import_options(old_options, session, report)
        if logs:
            _import_logs(old, session, report)
        for table in NOT_IMPORTED:
            n = len(old.rows(table, order="rowid"))
            if n:
                report.warn(f"{table}: {n} row(s) not imported (old UI feature)")
        session.commit()
        return report
    except BaseException:
        session.rollback()
        raise
    finally:
        old.close()


def _import_ingredients(old: _Old, session: Session, report: Report) -> None:
    names: set[str] = set()
    ids: set[int] = set()
    for r in old.rows("booze"):
        name = _text(r["name"]) or f"Ingredient {r['id']}"
        if name.lower() in names:
            report.warn(f"booze {r['id']}: duplicate name {name!r}, renamed to '{name} ({r['id']})'")
            name = f"{name} ({r['id']})"
        names.add(name.lower())
        old_type = int(_get(r, "type", 0))
        if old_type not in BOOZE_TYPES:
            report.warn(f"booze {r['id']} {name!r}: unknown type {old_type}, imported as 'other'")
        kind, manual = BOOZE_TYPES.get(old_type, (Kind.OTHER, False))
        abv = float(_get(r, "abv", 0) or 0)
        session.add(Ingredient(id=r["id"], name=name, brand=_text(_get(r, "brand")),
                               description=_text(_get(r, "desc")), abv=abv, kind=kind,
                               alcoholic=old_type == 1 or abv > 0, manual=manual, on_hand=manual))
        ids.add(r["id"])
    session.flush()
    report.counts["ingredients"] = len(ids)

    # booze groups: every booze in a group gets the group's "abstract" booze as its generic
    groups = {r["id"]: r for r in old.rows("booze_group")}
    linked = 0
    for r in old.rows("booze_group_booze"):
        group = groups.get(r["booze_group_id"])
        if group is None:
            report.warn(f"booze_group_booze {r['id']}: group {r['booze_group_id']} doesn't exist, skipped")
            continue
        generic, specific = group["abstract_booze_id"], r["booze_id"]
        if generic not in ids or specific not in ids or generic == specific:
            report.warn(f"booze_group_booze {r['id']}: bad booze ids ({specific} in {generic}), skipped")
            continue
        ing = session.get(Ingredient, specific)
        ing.generic_id = generic
        ing.generic_order = int(_get(r, "sequence", 0))
        linked += 1
    report.counts["brand links"] = linked


def _import_drinks(old: _Old, session: Session, report: Report) -> None:
    names = {r["id"]: r for r in old.rows("drink_name")}
    drink_ids: set[int] = set()
    for r in old.rows("drink"):
        n = names.get(r["name_id"])
        name = _text(n["name"]) if n is not None else ""
        if not name:
            name = f"Drink {r['id']}"
            report.warn(f"drink {r['id']}: no name, imported as {name!r}")
        # sugg_size is not imported: the old app never used it for pouring (glass size came from
        # the drink_size option), so carrying it over would change how big drinks are.
        session.add(Drink(id=r["id"], name=name,
                          sort_name=_text(_get(n, "sortname")) if n is not None else "",
                          description=_text(_get(r, "desc")),
                          popular=_bool(_get(r, "popular"), False),
                          enabled=_bool(_get(r, "available"), True), source="legacy"))
        drink_ids.add(r["id"])
    session.flush()
    report.counts["drinks"] = len(drink_ids)

    ingredient_ids = set(session.scalars(select(Ingredient.id)))
    items: dict[tuple[int, int], RecipeItem] = {}
    per_drink: dict[int, int] = {}
    for r in old.rows("drink_booze"):
        key = (r["drink_id"], r["booze_id"])
        parts = _get(r, "value", 0) or 0
        if key[0] not in drink_ids or key[1] not in ingredient_ids:
            report.warn(f"drink_booze {r['id']}: drink {key[0]} or booze {key[1]} doesn't exist, skipped")
            continue
        if parts <= 0:
            report.warn(f"drink {key[0]}: booze {key[1]} has {parts} parts, skipped")
            continue
        if key in items:
            report.warn(f"drink {key[0]}: booze {key[1]} listed twice, parts added together")
            items[key].parts += parts
            continue
        position = per_drink[key[0]] = per_drink.get(key[0], -1) + 1
        items[key] = RecipeItem(drink_id=key[0], ingredient_id=key[1], parts=float(parts), position=position)
        session.add(items[key])
    report.counts["recipe items"] = len(items)
    empty = drink_ids - {k[0] for k in items}
    if empty:
        report.warn(f"{len(empty)} drink(s) have no ingredients: ids {sorted(empty)}")


def _import_dispensers(old: _Old, session: Session, report: Report, sensors: bool) -> None:
    ingredient_ids = set(session.scalars(select(Ingredient.id)))
    n = 0
    for r in old.rows("dispenser"):
        number = r["id"]
        if not 1 <= number <= 15:
            report.warn(f"dispenser {number}: outside 1-15, skipped")
            continue
        booze = _get(r, "booze_id")
        if booze is not None and booze not in ingredient_ids:
            report.warn(f"dispenser {number}: booze {booze} doesn't exist, imported as empty")
            booze = None
        # The stored level only meant something with the sensors on (default databases say
        # "out" everywhere); otherwise start unknown until the next reading.
        level = OLD_LEVELS.get(_get(r, "out", 1), Level.UNKNOWN) if sensors else Level.UNKNOWN
        session.add(Dispenser(number=number, ingredient_id=booze, level=level))
        n += 1
    report.counts["dispensers"] = n


def _import_options(old_options: dict[str, object], session: Session, report: Report) -> None:
    n = 0
    for key, value in old_options.items():
        text = _text(value)
        if key == "login_passwd":
            if text:
                options.set_password(session, text)
                report.warn("admin password imported (now stored hashed)")
            continue
        if key not in options.DEFAULTS:
            report.warn(f"option {key}={text!r}: unknown, not imported")
            continue
        try:
            options.set(session, key, options.parse(key, text))
            n += 1
        except ValueError:
            report.warn(f"option {key}={text!r}: bad value, default used")
    report.counts["options"] = n


def _epoch(t) -> datetime:
    return datetime.fromtimestamp(int(t), timezone.utc).replace(tzinfo=None)


def _import_logs(old: _Old, session: Session, report: Report) -> None:
    drinks = set(session.scalars(select(Drink.id)))
    ingredients = set(session.scalars(select(Ingredient.id)))
    n = 0
    for r in old.rows("drink_log"):
        session.add(PourLog(time=_epoch(r["time"]), size_ml=float(r["size"]),
                            drink_id=r["drink_id"] if r["drink_id"] in drinks else None))
        n += 1
    for r in old.rows("shot_log"):
        session.add(PourLog(time=_epoch(r["time"]), size_ml=float(r["size"]),
                            ingredient_id=r["booze_id"] if r["booze_id"] in ingredients else None))
        n += 1
    report.counts["log entries"] = n
