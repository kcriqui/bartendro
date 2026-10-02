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
    assert len(data["drink"]) >= 50
    manual = {i["name"] for i in data["ingredient"] if i.get("manual")}
    for d in data["drink"]:
        # the bot pours a few liquids; the guest does the rest by hand, before or after
        pumped = [(a, u, n) for a, u, n, *_ in d["ingredients"] if u in recipes.UNITS_ML and n not in manual]
        assert 1 <= len(pumped) <= 8 and len(d["ingredients"]) >= 2, d["name"]
        after = [n for a, u, n, *s in d["ingredients"]
                 if (u not in recipes.UNITS_ML or n in manual) and s != ["before"]]
        assert pumped, d["name"]
        assert 45 <= sum(a * recipes.UNITS_ML[u] for a, u, _ in pumped) <= 250, d["name"]
        for a, u, n, *_ in d["ingredients"]:
            if u not in recipes.UNITS_ML:
                assert n in manual, (d["name"], n)  # counted amounts are never pumped
        text = (d.get("instructions", "") + " " + d.get("description", "")).lower()
        for word in ("shake ", "strain", "blend"):
            assert word not in text, (d["name"], word)  # those belong in `finish`
        if after or "shaker" in text or "mixing glass" in text:
            assert d.get("finish"), d["name"]  # say what to do after the pour
        prep = [n for a, u, n, *step in d["ingredients"] if step == ["before"] and u != "fill"]
        if prep:
            assert d.get("instructions"), d["name"]  # say what to do with the rinse / muddle
        assert "over ice" not in d.get("instructions", "").lower(), d["name"]  # ice is a checklist line


def test_sazerac(session):
    recipes.load(session)
    saz = drink(session, "Sazerac")
    pumped = [(i.ingredient.name, i.parts) for i in saz.items if not i.by_hand]
    by_hand = [(i.ingredient.name, i.hand_text, i.step) for i in saz.items if i.by_hand]
    assert pumped == [("Rye Whiskey", 60), ("Simple Syrup", 7.5)]
    assert by_hand == [("Absinthe", "1 dash", "before"), ("Peychaud's Bitters", "3 dashes", "after")]
    assert saz.size_ml == 68 and "absinthe" in saz.instructions.lower()
    session.add_all([Dispenser(number=1, ingredient=ing(session, "Rye Whiskey")),
                     Dispenser(number=2, ingredient=ing(session, "Simple Syrup"))])
    session.commit()
    # the bitters and absinthe need no dispenser, but must be on hand
    assert "Sazerac" not in {d.name for d in makeable_drinks(session)}
    away = {i.name: (by_hand, {d.name for d in ds}) for i, by_hand, ds in one_bottle_away(session, limit=50)}
    assert "Sazerac" not in away.get("Absinthe", (None, set()))[1]  # needs two things
    ing(session, "Absinthe").on_hand = True
    session.commit()
    away = {i.name: (by_hand, {d.name for d in ds}) for i, by_hand, ds in one_bottle_away(session, limit=50)}
    assert away["Peychaud's Bitters"] == (True, {"Sazerac", "Sazerac on the Rocks"})
    ing(session, "Peychaud's Bitters").on_hand = True
    session.commit()
    assert "Sazerac" in {d.name for d in makeable_drinks(session)}


def test_parse_amount():
    assert recipes.parse_amount("2") == (2, None, "", "after")
    assert recipes.parse_amount("30 ml") == (30, 30, "ml", "after")
    assert recipes.parse_amount("1 oz") == (pytest.approx(29.57), 1, "oz", "after")
    assert recipes.parse_amount("3 dashes") == (None, 3, "dash", "after")
    assert recipes.parse_amount("1 Barspoon") == (None, 1, "barspoon", "after")
    assert recipes.parse_amount("6 leaves before") == (None, 6, "leaf", "before")
    for bad in ("", "lots", "2 pints", "0", "1 2 3", "before"):
        with pytest.raises(ValueError):
            recipes.parse_amount(bad)


def test_specific_spirits_are_not_replaced_by_generic(session):
    recipes.load(session)
    tequila, reposado = ing(session, "Tequila"), ing(session, "Reposado Tequila")
    assert reposado.generic is tequila
    for name in ("Agave Syrup",):
        session.add(Dispenser(number=2, ingredient=ing(session, name)))
    ing(session, "Angostura Bitters").on_hand = True
    session.add(Dispenser(number=1, ingredient=tequila))
    session.commit()
    names = {d.name for d in makeable_drinks(session)}
    assert "Tequila Old Fashioned" not in names  # asks for reposado: plain tequila won't do
    session.get(Dispenser, 1).ingredient = reposado
    session.commit()
    names = {d.name for d in makeable_drinks(session)}
    assert "Tequila Old Fashioned" in names
    session.add(Dispenser(number=3, ingredient=ing(session, "Orange Juice")))
    session.add(Dispenser(number=4, ingredient=ing(session, "Grenadine")))
    session.commit()
    assert "Tequila Sunrise" in {d.name for d in makeable_drinks(session)}  # reposado does for tequila


def test_ice_and_both_sazeracs(session):
    recipes.load(session)
    assert ing(session, "Ice").on_hand and not ing(session, "Crushed Ice").on_hand
    screwdriver = drink(session, "Screwdriver")
    first = screwdriver.items[0]
    assert (first.ingredient.name, first.by_hand, first.step, first.hand_text) == \
        ("Ice", True, "before", "fill the glass with")
    assert first.parts is None  # ice isn't part of the mix: still 150 ml poured
    assert screwdriver.size_ml == 150
    neat, rocks = drink(session, "Sazerac"), drink(session, "Sazerac on the Rocks")
    assert "Ice" not in {i.ingredient.name for i in neat.items}
    assert [i.ingredient.name for i in rocks.items if i.step == "before"] == ["Absinthe", "Ice"]
    assert "No ice" in neat.instructions


def test_muddled_drinks_have_before_steps(session):
    recipes.load(session)
    mojito = drink(session, "Mojito")
    before = [(i.ingredient.name, i.hand_text) for i in mojito.items if i.by_hand and i.step == "before"]
    assert before == [("Mint", "6 leaves"), ("Ice", "fill the glass with")]
    caip = drink(session, "Caipirinha")
    assert [(i.ingredient.name, i.hand_text) for i in caip.items if i.by_hand] == \
        [("Lime", "4 wedges"), ("Sugar", "2 tsp"), ("Crushed Ice", "fill the glass with")]
    wr = drink(session, "White Russian")
    cream = [i for i in wr.items if i.ingredient.name == "Half and Half"][0]
    assert cream.by_hand and cream.parts == 30 and cream.step == "after"  # measured, by hand
    assert ing(session, "Half and Half").manual and not ing(session, "Half and Half").on_hand


def test_load_into_empty_database(session):
    report = recipes.load(session)
    n = len(recipes.read()["drink"])
    assert report.counts["drinks added"] == n
    screwdriver = drink(session, "Screwdriver")
    assert screwdriver.source == "classics" and screwdriver.popular and screwdriver.glass == "highball"
    assert screwdriver.size_ml == 150 and drink(session, "Godmother").size_ml == 70
    assert [(i.ingredient.name, i.parts, i.amount, i.unit) for i in screwdriver.items] == \
        [("Ice", None, 1, "fill"), ("Vodka", 50, 50, "ml"), ("Orange Juice", 100, 100, "ml")]
    assert ing(session, "Scotch Whisky").generic is ing(session, "Whiskey")
    # loading again changes nothing
    again = recipes.load(session)
    assert again.counts["drinks added"] == 0 and again.counts["drinks already there"] == n
    assert again.counts["ingredients added"] == 0
    assert session.scalar(select(func.count()).select_from(Drink)) == n


def test_update_refreshes_only_classics(session):
    recipes.load(session)
    d = drink(session, "Screwdriver")
    d.items[1].parts = 99
    d.description = "changed"
    session.commit()
    recipes.load(session)  # without update: left alone
    assert drink(session, "Screwdriver").description == "changed"
    report = recipes.load(session, update=True)
    assert report.counts["drinks updated"] == len(recipes.read()["drink"])
    d = drink(session, "Screwdriver")
    assert d.description != "changed" and d.items[1].parts == 50


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
    assert len(bottles) == 3 and len(drinks) >= 3
    needed = {i.ingredient_id for d in drinks for i in d.items if not i.by_hand}
    assert needed <= {b.id for b in bottles}  # every suggested drink runs on those 3 pumps
    tequila = ing(session, "Tequila").id
    bottles, drinks = suggest_bottles(session, 3, keep=[tequila])
    assert bottles[0].name == "Tequila" and len(drinks) >= 1


def test_one_bottle_away(session):
    recipes.load(session)
    vodka = ing(session, "Vodka")
    session.add(Dispenser(number=1, ingredient=vodka))
    session.commit()
    away = {i.name: {d.name for d in ds} for i, _, ds in one_bottle_away(session, limit=50)}
    assert away["Orange Juice"] == {"Screwdriver"}
    assert away["Tonic Water"] == {"Vodka Tonic"}
    assert "Gin" not in away  # gin drinks need more than one more bottle
