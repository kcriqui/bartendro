from pathlib import Path

import pytest
from sqlalchemy import func, select

from bartendro.db import open_db
from bartendro.db import recipes
from bartendro.db.importer import import_legacy
from bartendro.db.menu import makeable_drinks, one_bottle_away, suggest_bottles
from bartendro.db.models import Dispenser, Drink, Ingredient

DEFAULT_DB = Path(__file__).parent.parent / "ui" / "bartendro.db.default"


@pytest.fixture
def session(tmp_path):
    Session = open_db(tmp_path / "r.db")
    with Session() as s:
        yield s


def ing(s, name):
    return s.scalar(select(Ingredient).where(Ingredient.name == name))


def drink(s, name):
    return s.scalar(select(Drink).where(Drink.name == name))


def test_bundled_file_is_valid_and_simple():
    data = recipes.read()
    assert len(data["drink"]) >= 45
    manual = {i["name"] for i in data["ingredient"] if i.get("manual")}
    for d in data["drink"]:
        # the bot pours a few liquids; the guest adds dashes and shakes/stirs after the pour
        assert 2 <= len(d["ingredients"]) <= 8, d["name"]
        total = sum(a * recipes.UNITS_ML[u] for a, u, _ in d["ingredients"] if u in recipes.UNITS_ML)
        assert 45 <= total <= 250, d["name"]
        for _, unit, name in d["ingredients"]:
            assert (unit in recipes.UNITS_ML) == (name not in manual), (d["name"], name)  # dashes by hand
        text = (d.get("instructions", "") + " " + d.get("description", "")).lower()
        for word in ("shake ", "stir", "strain", "muddle", "blend"):
            assert word not in text, (d["name"], word)  # those belong in `finish`
        hand = [n for _, u, n in d["ingredients"] if u not in recipes.UNITS_ML]
        if hand or "shaker" in text or "mixing glass" in text:
            assert d.get("finish"), d["name"]


def test_sazerac(session):
    recipes.load(session)
    saz = drink(session, "Sazerac")
    pumped = [(i.ingredient.name, i.parts) for i in saz.items if not i.by_hand]
    by_hand = [(i.ingredient.name, i.hand_text) for i in saz.items if i.by_hand]
    assert pumped == [("Rye Whiskey", 60), ("Simple Syrup", 7.5)]
    assert by_hand == [("Peychaud's Bitters", "3 dashes"), ("Absinthe", "1 dash")]
    assert saz.size_ml == 68 and "absinthe" in saz.finish.lower()
    # makeable with rye and syrup on pumps: the bitters and absinthe don't need a dispenser
    session.add_all([Dispenser(number=1, ingredient=ing(session, "Rye Whiskey")),
                     Dispenser(number=2, ingredient=ing(session, "Simple Syrup"))])
    session.commit()
    assert "Sazerac" in {d.name for d in makeable_drinks(session)}


def test_parse_amount():
    assert recipes.parse_amount("2") == (2, None, "")
    assert recipes.parse_amount("30 ml") == (30, 30, "ml")
    assert recipes.parse_amount("1 oz") == (pytest.approx(29.57), 1, "oz")
    assert recipes.parse_amount("3 dashes") == (None, 3, "dash")
    assert recipes.parse_amount("1 Barspoon") == (None, 1, "barspoon")
    for bad in ("", "lots", "2 pints", "0", "1 2 3"):
        with pytest.raises(ValueError):
            recipes.parse_amount(bad)


def test_load_into_empty_database(session):
    report = recipes.load(session)
    n = len(recipes.read()["drink"])
    assert report.counts["drinks added"] == n
    screwdriver = drink(session, "Screwdriver")
    assert screwdriver.source == "classics" and screwdriver.popular and screwdriver.glass == "highball"
    assert screwdriver.size_ml == 150 and drink(session, "Godmother").size_ml == 70
    assert [(i.ingredient.name, i.parts, i.amount, i.unit) for i in screwdriver.items] == \
        [("Vodka", 50, 50, "ml"), ("Orange Juice", 100, 100, "ml")]
    assert ing(session, "Scotch Whisky").generic is ing(session, "Whiskey")
    # loading again changes nothing
    again = recipes.load(session)
    assert again.counts["drinks added"] == 0 and again.counts["drinks already there"] == n
    assert again.counts["ingredients added"] == 0
    assert session.scalar(select(func.count()).select_from(Drink)) == n


def test_update_refreshes_only_classics(session):
    recipes.load(session)
    d = drink(session, "Screwdriver")
    d.items[0].parts = 99
    d.description = "changed"
    session.commit()
    recipes.load(session)  # without update: left alone
    assert drink(session, "Screwdriver").description == "changed"
    report = recipes.load(session, update=True)
    assert report.counts["drinks updated"] == len(recipes.read()["drink"])
    d = drink(session, "Screwdriver")
    assert d.description != "changed" and d.items[0].parts == 50


def test_load_into_imported_old_database(session):
    import_legacy(DEFAULT_DB, session)
    before = {d.id for d in makeable_drinks(session)}
    report = recipes.load(session)
    assert report.counts["drinks already there"] >= 5   # Screwdriver, Cape Cod, ... kept as they were
    assert drink(session, "Screwdriver").source == "legacy"
    assert "using your 'Rum, Light' for 'White Rum'" in report.notes
    assert ing(session, "Kahlua").generic.name == "Coffee Liqueur"
    assert ing(session, "Cointreau").generic.name == "Triple Sec"
    assert ing(session, "White Rum") is None             # matched, not duplicated
    # Kahlua on #2 now counts as Coffee Liqueur; Amaretto on #11: these classics are makeable
    after = {d.name for d in makeable_drinks(session)}
    assert {"Godmother", "Madras"} <= after
    assert len(after) > len(before)


def test_bad_recipe_files(tmp_path):
    base = '[[ingredient]]\nname = "Vodka"\n'
    cases = {
        'name = "X"\ningredients = [[50, "pint", "Vodka"]]': "unit",
        'name = "X"\ningredients = [[2, "dash", "Vodka"]]': "nothing for the pumps",
        'name = "X"\ningredients = [[50, "ml", "Gin"]]': "not an",
        'name = "X"\ningredients = [[50, "ml", "Vodka"], [10, "ml", "vodka"]]': "twice",
        'name = "X"\ningredients = [[-5, "ml", "Vodka"]]': "amount",
        'name = "X"': "needs a name",
    }
    for body, msg in cases.items():
        f = tmp_path / "bad.toml"
        f.write_text(base + "[[drink]]\n" + body + "\n")
        with pytest.raises(recipes.RecipeFileError, match=msg):
            recipes.read(f)


def test_suggest_bottles_for_a_small_bot(session):
    recipes.load(session)
    bottles, drinks = suggest_bottles(session, 3)
    assert {b.name for b in bottles} == {"Vodka", "Orange Juice", "Cranberry Juice"}
    assert {d.name for d in drinks} == {"Screwdriver", "Cape Cod", "Madras"}
    tequila = ing(session, "Tequila").id
    bottles, drinks = suggest_bottles(session, 3, keep=[tequila])
    assert bottles[0].name == "Tequila" and len(drinks) >= 1


def test_one_bottle_away(session):
    recipes.load(session)
    vodka = ing(session, "Vodka")
    session.add(Dispenser(number=1, ingredient=vodka))
    session.commit()
    away = {i.name: {d.name for d in ds} for i, ds in one_bottle_away(session, limit=50)}
    assert away["Orange Juice"] == {"Screwdriver"}
    assert away["Tonic Water"] == {"Vodka Tonic"}
    assert "Gin" not in away  # gin drinks need more than one more bottle
