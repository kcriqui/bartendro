import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from bartendro.db import make_engine, open_db, upgrade
from bartendro.db import options
from bartendro.db.importer import ImportError_, import_legacy
from bartendro.db.menu import available_ingredients, makeable_drinks, scale_recipe
from bartendro.db.models import (Base, Dispenser, Drink, Ingredient, Kind, Level, PourLog,
                                 RecipeItem)
from bartendro.dbcli import main as dbcli

DEFAULT_DB = Path(__file__).parent.parent / "ui" / "bartendro.db.default"


@pytest.fixture
def session(tmp_path):
    Session = open_db(tmp_path / "new.db")
    with Session() as s:
        yield s


# ------------------------------------------------------------------ schema


def test_migrations_match_models(tmp_path):
    engine = make_engine(tmp_path / "m.db")
    upgrade(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], "models.py changed without a migration: bartendro-db revision -m ..."


def test_downgrade_and_upgrade_again(tmp_path):
    engine = make_engine(tmp_path / "m.db")
    upgrade(engine)
    from alembic import command

    from bartendro.db import _alembic_config
    command.downgrade(_alembic_config(engine), "base")
    upgrade(engine)


def test_upgrade_keeps_data(tmp_path):
    """Migrations rebuild tables on SQLite; rows in other tables that point at them (recipe
    lines, dispensers, the pour log) must survive - no foreign key errors, no cascades."""
    path = tmp_path / "m.db"
    engine = make_engine(path)
    upgrade(engine, "0001")
    c = sqlite3.connect(path)
    c.executescript("""
        INSERT INTO ingredient (id, name, brand, description, abv, kind, manual, generic_id, generic_order)
            VALUES (1, 'Vodka', '', '', 40, 'alcohol', 0, NULL, 0),
                   (2, 'Titos', '', '', 40, 'alcohol', 0, 1, 0),
                   (3, 'Mint', '', '', 0, 'other', 1, NULL, 0);
        INSERT INTO drink (id, name, sort_name, description, popular, enabled, size_ml)
            VALUES (1, 'Vodka Mint', '', '', 1, 1, NULL);
        INSERT INTO recipe_item (id, drink_id, ingredient_id, parts, position)
            VALUES (1, 1, 1, 2, 0), (2, 1, 3, 1, 1);
        INSERT INTO dispenser (number, ingredient_id, level, ticks_per_ml) VALUES (1, 2, 'ok', 3.0);
        INSERT INTO pour_log (id, time, drink_id, ingredient_id, size_ml)
            VALUES (1, '2026-01-01 00:00:00', 1, NULL, 150), (2, '2026-01-01 00:01:00', NULL, 2, 30);
        INSERT INTO option (key, value) VALUES ('metric', '1');
    """)
    c.commit()
    c.close()
    upgrade(engine)
    c = sqlite3.connect(path)
    assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0006"
    assert c.execute("SELECT name, kind, alcoholic FROM ingredient ORDER BY id").fetchall() == \
        [("Vodka", "other", 1), ("Titos", "other", 1), ("Mint", "other", 0)]  # kind alcohol -> alcoholic
    assert c.execute("SELECT count(*) FROM recipe_item").fetchone()[0] == 2
    assert c.execute("SELECT drink_id FROM pour_log ORDER BY id").fetchall() == [(1,), (None,)]
    assert c.execute("SELECT generic_id FROM ingredient WHERE id = 2").fetchone()[0] == 1
    assert c.execute("SELECT ingredient_id, ticks_per_ml FROM dispenser").fetchall() == [(2, 3.0)]
    assert c.execute("SELECT name, on_hand FROM ingredient ORDER BY id").fetchall() == \
        [("Vodka", 0), ("Titos", 0), ("Mint", 1)]  # manual ones start on hand
    assert c.execute("SELECT step FROM recipe_item").fetchall() == [("after",), ("after",)]
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not c.execute("SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%'").fetchall()


def test_failed_migration_changes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    engine = make_engine(path)
    upgrade(engine, "0003")
    with pytest.raises(RuntimeError, match="boom"):
        # break the last step of 0004 half way through
        from alembic.operations import Operations
        real = Operations.batch_alter_table

        def boom(self, table, **kw):
            if table == "recipe_item":
                raise RuntimeError("boom")
            return real(self, table, **kw)
        monkeypatch.setattr(Operations, "batch_alter_table", boom)
        upgrade(engine)
    monkeypatch.undo()
    c = sqlite3.connect(path)
    assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003"
    cols = [r[1] for r in c.execute("PRAGMA table_info(ingredient)")]
    assert "on_hand" not in cols  # the first half of 0004 was rolled back too
    c.close()
    upgrade(engine)  # and it still upgrades cleanly afterwards


def test_upgrade_refuses_old_database(tmp_path):
    old = tmp_path / "old.db"
    old.write_bytes(DEFAULT_DB.read_bytes())
    with pytest.raises(RuntimeError, match="bartendro-db import"):
        upgrade(make_engine(old))


# ------------------------------------------------------------------ options


def test_options_defaults_and_types(session):
    assert options.get(session, "drink_size") == 150
    assert options.get(session, "metric") is False
    options.set(session, "metric", True)
    options.set(session, "drink_size", "180")
    session.commit()
    assert options.get(session, "metric") is True
    assert options.get(session, "drink_size") == 180
    with pytest.raises(KeyError):
        options.get(session, "nope")
    with pytest.raises(ValueError):
        options.set(session, "drink_size", "big")


def test_password_is_hashed(session):
    options.set_password(session, "s3cret")
    assert "s3cret" not in options.get(session, "login_password_hash")
    assert options.check_password(session, "s3cret")
    assert not options.check_password(session, "wrong")


# ------------------------------------------------------------- import: default db


def _old_makeable(path: Path) -> set[int]:
    """The old mixer.get_available_drink_list, as SQL on the old file (sensors off,
    15 dispensers): every booze of the drink is on a dispenser, an external booze, or the
    abstract booze of a group with a member on a dispenser."""
    c = sqlite3.connect(path)
    have = {r[0] for r in c.execute("SELECT booze_id FROM dispenser")}
    have |= {r[0] for r in c.execute("SELECT id FROM booze WHERE type = 4")}
    have |= {r[0] for r in c.execute(
        "SELECT abstract_booze_id FROM booze_group WHERE id IN (SELECT booze_group_id "
        "FROM booze_group_booze bgb, dispenser d WHERE bgb.booze_id = d.booze_id)")}
    needs: dict[int, set[int]] = {}
    for drink, booze in c.execute("SELECT drink_id, booze_id FROM drink_booze"):
        needs.setdefault(drink, set()).add(booze)
    return {d for d, b in needs.items() if b <= have}


def test_import_default_database(session):
    before = hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest()
    report = import_legacy(DEFAULT_DB, session)
    assert hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() == before  # old file untouched
    assert report.counts == {"ingredients": 59, "brand links": 0, "drinks": 83, "recipe items": 247,
                             "dispensers": 15, "options": 14, "log entries": 0}

    vodka = session.get(Ingredient, 1)
    assert (vodka.name, vodka.alcoholic, vodka.abv) == ("Vodka", True, 40.0)
    assert session.get(Ingredient, 4).name == "Orange Juice" and not session.get(Ingredient, 4).alcoholic
    assert session.get(Dispenser, 1).ingredient is vodka
    assert session.get(Dispenser, 1).level is Level.UNKNOWN  # sensors were off
    martini = session.get(Drink, 1)
    assert martini.name == "Sour Apple Martini" and martini.popular and martini.enabled
    assert {(i.ingredient.name, i.parts) for i in martini.items} == {("Vodka", 1), ("Sour Apple Pucker", 1)}

    assert options.get(session, "drink_size") == 150
    assert options.get(session, "use_shotbot_ui") is True
    assert options.check_password(session, "boozemeup")

    # same drinks as the old app would offer (it didn't filter on "available")
    made = {d.id for d in makeable_drinks(session, enabled_only=False)}
    assert made == _old_makeable(DEFAULT_DB)
    assert len(made) == 41
    assert len(makeable_drinks(session)) == 38  # 3 of them are switched off for the menu


def test_import_refuses_non_empty_database_unless_replace(session):
    import_legacy(DEFAULT_DB, session)
    with pytest.raises(ImportError_, match="--replace"):
        import_legacy(DEFAULT_DB, session)
    report = import_legacy(DEFAULT_DB, session, replace=True)
    assert report.counts["drinks"] == 83
    assert len(session.scalars(select(Drink)).all()) == 83


def test_import_rejects_non_bartendro_files(tmp_path, session):
    junk = tmp_path / "junk.db"
    junk.write_text("not a database")
    with pytest.raises(ImportError_):
        import_legacy(junk, session)
    with pytest.raises(ImportError_, match="does not exist"):
        import_legacy(tmp_path / "missing.db", session)


# ------------------------------------------------------------ import: odd old files


def make_old_db(path: Path, sensors: bool = True) -> Path:
    """An early-schema old database (no booze.type, drink.available or dispenser.out columns
    in some tables) with the awkward cases the importer has to handle."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE booze (id INTEGER PRIMARY KEY, name TEXT NOT NULL, brand TEXT,
                            "desc" TEXT NOT NULL, abv INTEGER, type INTEGER DEFAULT 0);
        CREATE TABLE drink_name (id INTEGER PRIMARY KEY, name TEXT NOT NULL, sortname TEXT NOT NULL,
                                 is_common INTEGER);
        CREATE TABLE drink (id INTEGER PRIMARY KEY, "desc" TEXT NOT NULL, name_id INTEGER NOT NULL,
                            popular integer default f);
        CREATE TABLE drink_booze (id INTEGER PRIMARY KEY, drink_id INTEGER NOT NULL,
                                  booze_id INTEGER NOT NULL, value INTEGER, unit INTEGER);
        CREATE TABLE dispenser (id INTEGER PRIMARY KEY, booze_id INTEGER NOT NULL, actual integer default 0,
                                out INTEGER DEFAULT 0);
        CREATE TABLE booze_group (id INTEGER PRIMARY KEY, abstract_booze_id INTEGER NOT NULL, name TEXT NOT NULL);
        CREATE TABLE booze_group_booze (id INTEGER PRIMARY KEY, booze_group_id INTEGER NOT NULL,
                                        booze_id INTEGER NOT NULL, sequence INTEGER);
        CREATE TABLE option (id INTEGER PRIMARY KEY, "key" TEXT NOT NULL, value TEXT);
        CREATE TABLE drink_log (id INTEGER PRIMARY KEY, drink_id INTEGER NOT NULL, time INTEGER NOT NULL,
                                size INTEGER NOT NULL);
        CREATE TABLE shot_log (id INTEGER PRIMARY KEY, booze_id INTEGER NOT NULL, time INTEGER NOT NULL,
                               size INTEGER NOT NULL);

        INSERT INTO booze VALUES (1, 'Vodka', '', 'generic vodka', 40, 1),
                                 (2, 'Titos', 'Tito''s', '', 40, 1),
                                 (3, 'Lime Juice', '', '', 0, 2),
                                 (4, 'Simple Syrup', '', '', 0, 3),
                                 (5, 'Mint Leaves', '', '', 0, 4),
                                 (6, 'vodka ', '', 'duplicate name', 40, 1),
                                 (7, 'Mystery', '', '', 0, 9);
        INSERT INTO booze_group VALUES (1, 1, 'Vodka');
        INSERT INTO booze_group_booze VALUES (1, 1, 2, 1), (2, 7, 3, 1);
        INSERT INTO drink_name VALUES (1, 'Vodka Gimlet', 'Gimlet, Vodka', 0), (2, 'Mojito-ish', '', 0);
        INSERT INTO drink VALUES (1, 'tart', 1, 'f'), (2, 'minty', 2, 1), (3, 'nameless', 99, 0);
        INSERT INTO drink_booze VALUES (1, 1, 1, 2, 0), (2, 1, 3, 1, 0), (3, 1, 4, 1, 0),
                                       (4, 2, 2, 1, 0), (5, 2, 5, 1, 0), (6, 2, 4, 0, 0),
                                       (7, 2, 2, 1, 0), (8, 1, 42, 1, 0), (9, 3, 3, 1, 0);
        INSERT INTO dispenser VALUES (1, 2, 60, 1), (2, 3, 60, 2), (3, 4, 60, 0), (4, 42, 60, 1);
        INSERT INTO drink_log VALUES (1, 1, 1500000000, 150), (2, 77, 1500000100, 120);
        INSERT INTO shot_log VALUES (1, 2, 1500000200, 30);
    """)
    c.execute("INSERT INTO option (key, value) VALUES ('use_liquid_level_sensors', ?), ('metric', '1'), "
              "('drink_size', 'huge'), ('login_passwd', ''), ('old_thing', 'x')", ("1" if sensors else "0",))
    c.commit()
    c.close()
    return path


def test_import_odd_old_database(tmp_path, session):
    report = import_legacy(make_old_db(tmp_path / "old.db"), session)
    w = "\n".join(report.warnings)

    ing = {i.id: i for i in session.scalars(select(Ingredient))}
    assert ing[2].generic is ing[1] and ing[1].specifics == [ing[2]]  # booze group -> brand link
    assert (ing[3].kind, ing[4].kind) == (Kind.TART, Kind.SWEET)
    assert ing[5].manual and ing[5].kind is Kind.OTHER                # External
    assert ing[6].name == "vodka (6)" and "duplicate name" in w
    assert ing[7].kind is Kind.OTHER and "unknown type 9" in w
    assert "group 7 doesn't exist" in w

    gimlet, mojito, nameless = (session.get(Drink, i) for i in (1, 2, 3))
    assert gimlet.sort_name == "Gimlet, Vodka" and not gimlet.popular and gimlet.enabled
    assert mojito.popular
    assert nameless.name == "Drink 3" and "no name" in w
    assert [(i.ingredient_id, i.parts) for i in gimlet.items] == [(1, 2), (3, 1), (4, 1)]
    assert [(i.ingredient_id, i.parts) for i in mojito.items] == [(2, 2), (5, 1)]  # dup added, 0 dropped
    assert "listed twice" in w and "0 parts" in w and "booze 42 doesn't exist" in w

    d = {x.number: x for x in session.scalars(select(Dispenser))}
    assert [(d[n].ingredient_id, d[n].level) for n in (1, 2, 3)] == \
        [(2, Level.OK), (3, Level.LOW), (4, Level.OUT)]
    assert d[4].ingredient_id is None

    assert options.get(session, "metric") is True
    assert options.get(session, "drink_size") == 150 and "drink_size='huge'" in w
    assert options.get(session, "login_password_hash") == ""  # empty old password: none set
    assert "old_thing" in w

    logs = session.scalars(select(PourLog).order_by(PourLog.time)).all()
    assert [(p.drink_id, p.ingredient_id, p.size_ml) for p in logs] == [(1, None, 150), (None, None, 120), (None, 2, 30)]
    assert logs[0].time.year == 2017


def test_levels_ignored_when_sensors_were_off(tmp_path, session):
    import_legacy(make_old_db(tmp_path / "old.db", sensors=False), session)
    assert {d.level for d in session.scalars(select(Dispenser))} == {Level.UNKNOWN}


# ------------------------------------------------------------------ menu


def test_generic_and_manual_ingredients_make_drinks_available(tmp_path, session):
    import_legacy(make_old_db(tmp_path / "old.db"), session)
    # Tito's on #1 counts as Vodka; Mint Leaves are manual; Simple Syrup's dispenser is out
    assert {1, 2, 3, 5} <= available_ingredients(session)
    assert 4 not in available_ingredients(session)
    assert [d.name for d in makeable_drinks(session)] == ["Drink 3", "Mojito-ish"]  # gimlet needs syrup
    options.set(session, "use_liquid_level_sensors", False)
    assert {d.id for d in makeable_drinks(session)} == {1, 2, 3}
    assert {d.id for d in makeable_drinks(session, dispenser_count=1)} == {2}  # only Tito's + mint


def test_menu_sorting_and_disabled_drinks(tmp_path, session):
    import_legacy(make_old_db(tmp_path / "old.db", sensors=False), session)
    session.get(Drink, 3).enabled = False
    names = [d.name for d in makeable_drinks(session)]
    assert names == ["Vodka Gimlet", "Mojito-ish"]  # sorted by sort_name "Gimlet, Vodka"
    assert 3 in {d.id for d in makeable_drinks(session, enabled_only=False)}


def _drink(session, *items):
    d = Drink(name="test")
    for n, (kind, parts, manual) in enumerate(items):
        alcoholic = kind == "booze"
        ing = Ingredient(name=f"i{n}", kind=Kind.OTHER if alcoholic else kind, alcoholic=alcoholic,
                         manual=manual)
        d.items.append(RecipeItem(ingredient=ing, parts=parts))
    session.add(d)
    session.flush()
    return d, [i.ingredient_id for i in d.items]


def test_scale_recipe(session):
    d, (vodka, lime, syrup) = _drink(session, ("booze", 2, False), (Kind.TART, 1, False),
                                     (Kind.SWEET, 1, False))
    assert scale_recipe(d, 160) == pytest.approx({vodka: 80, lime: 40, syrup: 40})
    # stronger: alcohol 2 * 1.25 = 2.5 parts of 4.5
    assert scale_recipe(d, 180, strength=1) == pytest.approx({vodka: 100, lime: 40, syrup: 40})
    # tarter: lime 1.25, syrup 0.75
    assert scale_recipe(d, 160, tartness=1) == pytest.approx({vodka: 80, lime: 50, syrup: 30})


def test_scale_recipe_leaves_out_manual_ingredients(session):
    d, (rum, mint) = _drink(session, ("booze", 3, False), (Kind.OTHER, 1, True))
    assert scale_recipe(d, 120, include_manual=False) == pytest.approx({rum: 90})  # mint counts toward the mix
    assert scale_recipe(d, 120) == pytest.approx({rum: 90, mint: 30})


# ------------------------------------------------------------------ CLI


def test_dbcli(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert dbcli(["--db", db, "upgrade"]) == 0
    assert "schema none -> 0006" in capsys.readouterr().out
    assert dbcli(["--db", db, "import", str(DEFAULT_DB)]) == 0
    assert "83 drinks" in capsys.readouterr().out
    assert dbcli(["--db", db, "import", str(DEFAULT_DB)]) == 2  # already has data
    assert dbcli(["--db", db, "show", "--dispensers", "3"]) == 0
    out = capsys.readouterr().out
    assert "#3   Baileys" in out and "#4" not in out and "Black Russian" in out
    assert dbcli(["--db", str(DEFAULT_DB), "upgrade"]) == 2  # old file: use import


def test_import_recipes_and_bottles_only(tmp_path, session):
    report = import_legacy(make_old_db(tmp_path / "old.db"), session, logs=False, settings=False)
    assert "log entries" not in report.counts and "options" not in report.counts
    assert session.scalars(select(PourLog)).all() == []
    assert options.get(session, "metric") is False  # the old db's metric=1 not imported
    assert len(session.scalars(select(Drink)).all()) == 3
    assert session.get(Dispenser, 1).ingredient_id == 2
