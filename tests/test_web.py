import time
from pathlib import Path

import pytest
from markupsafe import escape
from fastapi.testclient import TestClient
from sqlalchemy import select

from bartendro import bot as bot_mod
from bartendro.bot import Bot
from bartendro.db import open_db, options
from bartendro.db.importer import import_legacy
from bartendro.db.models import Dispenser, Drink, Ingredient, Party
from bartendro.hw.driver import Driver
from bartendro.hw.simulator import SimBus
from bartendro.web import create_app

DEFAULT_DB = Path(__file__).parent.parent / "ui" / "bartendro.db.default"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(SimBus, "time_scale", 1000.0)
    monkeypatch.setattr(bot_mod, "CLEAN_SECONDS", 0)
    monkeypatch.setattr(bot_mod, "CLEAN_STAGGER", 0)
    monkeypatch.setattr(bot_mod, "LED_DONE_SECONDS", 0.01)


@pytest.fixture
def env(tmp_path):
    sessions = open_db(tmp_path / "web.db")
    with sessions() as s:
        import_legacy(DEFAULT_DB, s)
    bus = SimBus.with_dispensers(15)
    driver = Driver(bus.serial, bus.router)
    driver.discover()
    b = Bot(driver, sessions)
    b.start()
    with TestClient(create_app(b, "Testbot")) as client:
        yield client, b, bus, sessions


def wait_idle(b, timeout=5):
    end = time.monotonic() + timeout
    while b.status()["busy"] and time.monotonic() < end:
        time.sleep(0.01)
    assert not b.status()["busy"]


def black_russian(sessions):
    with sessions() as s:
        return s.scalar(select(Drink.id).where(Drink.name == "Black Russian"))


def test_pages_render(env):
    client, b, _, sessions = env
    d = black_russian(sessions)
    for url in ["/", "/menu/all", "/menu/vodka", f"/drink/{d}", "/shots", "/admin", "/admin/drinks",
                f"/admin/drink/{d}",
                "/admin/drink/new", "/admin/ingredients", "/admin/ingredient/1", "/admin/ingredient/new",
                "/admin/options", "/admin/log", "/static/app.js", "/static/style.css"]:
        r = client.get(url)
        assert r.status_code == 200, url
    menu = client.get("/").text
    assert "Testbot" in menu and "the essentials" in menu and "the menu" in menu
    assert '/menu/vodka' in menu and '/menu/all' in menu
    everything = client.get("/menu/all").text
    assert "Black Russian" in everything
    assert "Tequila Sunrise" not in everything  # no tequila on the dispensers
    vodka = client.get("/menu/vodka").text
    assert "Black Russian" in vodka and "Amaretto Sour" not in vodka
    assert client.get("/menu/no-such-section", follow_redirects=False).status_code == 303
    assert 'class="on"' in client.get("/admin/drinks").text  # tab highlighted


def test_classic_recipes_and_plan(env):
    client, b, _, sessions = env
    r = client.post("/admin/recipes/load", follow_redirects=False)
    assert r.status_code == 303 and "loaded=" in r.headers["location"]
    assert "Added" in client.get(r.headers["location"]).text
    with sessions() as s:
        godmother = s.scalar(select(Drink).where(Drink.name == "Godmother"))
        assert godmother.source == "classics"
    page = client.get(f"/drink/{godmother.id}").text
    assert "old fashioned glass" in page
    plan = client.get(f"/api/drink/{godmother.id}/plan").json()
    assert plan["before"] == [{"ingredient": "Ice", "ml": None, "text": "fill the glass with"}]
    plan = client.get(f"/api/drink/{godmother.id}/plan").json()  # Vodka #1, Amaretto #11
    assert {p["dispenser"] for p in plan["pumps"]} == {1, 11}
    assert "One step away" in client.get("/admin").text
    plan_page = client.get("/admin/plan?pumps=3").text
    assert "With <b>" in plan_page


def test_make_drink_api_and_websocket(env):
    client, b, bus, sessions = env
    d = black_russian(sessions)
    plan = client.get(f"/api/drink/{d}/plan", params={"size_ml": 120, "strength": 1}).json()
    assert plan["name"] == "Black Russian" and {p["dispenser"] for p in plan["pumps"]} == {1, 2}
    assert sum(p["ml"] for p in plan["pumps"]) == pytest.approx(120, abs=0.2)

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["state"] == "ready"
        r = client.post(f"/api/drink/{d}/make", json={"size_ml": 120})
        assert r.status_code == 202 and r.json()["pouring"] == "Black Russian"
        types = []
        while "done" not in types or types[-1] != "status":
            types.append(ws.receive_json()["type"])
        assert types[:2] == ["status", "pouring"]
    wait_idle(b)
    assert bus.ports[0].poured_ticks > 0 and bus.ports[1].poured_ticks > 0


def test_api_errors(env):
    client, b, _, sessions = env
    assert client.post("/api/drink/9999/make", json={}).status_code == 400
    r = client.post(f"/api/drink/{black_russian(sessions)}/make", json={"strength": 5})
    assert r.status_code == 400 and "strength" in r.json()["error"]
    assert client.post("/api/shot/99").status_code == 400
    b._lock.acquire()  # something else is pouring
    try:
        r = client.post("/api/shot/1")
        assert r.status_code == 409 and "busy" in r.json()["error"]
    finally:
        b._lock.release()


def test_shot_test_run_clean_reset(env):
    client, b, bus, _ = env
    assert client.post("/api/shot/3").status_code == 202
    wait_idle(b)
    assert bus.ports[2].poured_ticks > 0
    assert client.post("/api/dispenser/4/test", json={"ml": 5}).status_code == 202
    wait_idle(b)
    assert client.post("/api/dispenser/5/run", json={"ms": 1, "reverse": True}).status_code == 202
    wait_idle(b)
    assert client.post("/api/clean", json={"which": "right"}).status_code == 202
    wait_idle(b)
    assert client.post("/api/dispenser/6/clean").status_code == 202
    wait_idle(b)
    page = client.get("/admin").text  # the dispensers page
    assert 'data-post="/api/dispenser/6/clean"' in page and "cleaning solution" in page
    assert client.post("/api/check-levels").status_code == 202
    wait_idle(b)
    assert client.post("/api/reset").json()["state"] == "ready"


def test_admin_dispensers_save(env):
    client, b, _, sessions = env
    form = {f"ingredient{n}": "" for n in range(1, 16)}
    form.update({"ingredient1": "6", "cal1": "3.1"})  # Tequila on #1, everything else empty
    r = client.post("/admin/dispensers", data=form, follow_redirects=False)
    assert r.status_code == 303
    wait_idle(b)
    with sessions() as s:
        d1 = s.get(Dispenser, 1)
        assert (d1.ingredient.name, d1.ticks_per_ml) == ("Tequila", 3.1)
        assert s.get(Dispenser, 2).ingredient_id is None
    assert b.state.value == "hard_out"  # menu needs more than tequila


def rows(*lines):
    """Drink-editor form fields for [(ingredient, amount, unit[, step])]."""
    out = {"ing_name": [], "amount": [], "unit": [], "step": []}
    for name, amount, unit, *step in lines:
        out["ing_name"].append(name)
        out["amount"].append(amount)
        out["unit"].append(unit)
        out["step"].append(step[0] if step else "after")
    return out


def test_admin_drink_edit(env):
    client, b, _, sessions = env
    r = client.post("/admin/drink/new", data={
        "name": "Screwdriver Deluxe", "description": "OJ + vodka", "enabled": "on",
        **rows(("Vodka", "1", "parts"), ("orange juice", "2", "parts"), ("", "", "ml"), ("Vodka", "1", "parts"))},
        follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/drinks"
    with sessions() as s:
        d = s.scalar(select(Drink).where(Drink.name == "Screwdriver Deluxe"))
        assert {(i.ingredient_id, i.parts) for i in d.items} == {(1, 2), (4, 2)}  # vodka rows added
        assert d.enabled and not d.popular
        drink_id = d.id
    assert "Screwdriver Deluxe" in client.get("/menu/all").text
    assert client.post(f"/admin/drink/{drink_id}/toggle/popular").json() == {"popular": True}
    # edit it, keeping vodka (the old rows must go before the new ones are added); a new ingredient
    r = client.post(f"/admin/drink/{drink_id}", data={
        "name": "Screwdriver Deluxe", "enabled": "on", "category": "House specials",
        **rows(("Vodka", "30", "ml"), ("Orange Juice", "3", "oz"), ("Blood Orange", "2", "dash"))},
        follow_redirects=False)
    assert r.status_code == 303 and "created=Blood%20Orange" in r.headers["location"]
    with sessions() as s:
        d = s.get(Drink, drink_id)
        assert [(i.ingredient.name, round(i.parts or 0, 2), i.amount, i.unit) for i in d.items] == \
            [("Vodka", 30, 30, "ml"), ("Orange Juice", 88.71, 3, "oz"), ("Blood Orange", 0, 2, "dash")]
        assert d.category == "House specials" and not s.scalar(
            select(Ingredient).where(Ingredient.name == "Blood Orange")).alcoholic
    assert "House specials" in client.get("/admin/drinks").text
    page = client.get(f"/admin/drink/{drink_id}").text
    assert 'value="3"' in page and "<option selected>oz</option>" in page
    # duplicate: a switched-off copy to edit
    r = client.post(f"/admin/drink/{drink_id}/duplicate", follow_redirects=False)
    copy_id = int(r.headers["location"].rsplit("/", 1)[1])
    with sessions() as s:
        copy = s.get(Drink, copy_id)
        assert copy.name == "Screwdriver Deluxe (copy)" and not copy.enabled and len(copy.items) == 3
    assert client.post(f"/admin/drink/{drink_id}", data={
        "name": "X", **rows(("Vodka", "lots", "ml"))}).status_code == 400
    client.post(f"/admin/drink/{drink_id}", data={"delete": "1"})
    with sessions() as s:
        assert s.get(Drink, drink_id) is None


def test_drink_preview(env):
    client, *_ = env
    data = client.post("/api/drink-preview", json={"rows": [
        {"ingredient": "Vodka", "amount": "45", "unit": "ml"},
        {"ingredient": "Orange Juice", "amount": "90", "unit": "ml"},
        {"ingredient": "Angostura", "amount": "2", "unit": "dash"},
        {"ingredient": "Mystery Syrup", "amount": "1", "unit": "parts"},
        {"ingredient": "Ice", "amount": "", "unit": "fill", "step": "before"},
        {"ingredient": "Gin", "amount": "x", "unit": "ml"}]}).json()
    how = {l["ingredient"]: l["how"] for l in data["lines"]}
    assert how["Vodka"] == "pump" and how["Orange Juice"] == "pump"
    assert how["Mystery Syrup"] == "new" and how["Ice"] == "new"
    assert data["size_ml"] == 135 and data["errors"] == ["Gin: amount 'x' isn't a number"]
    vodka_ml = next(l["ml"] for l in data["lines"] if l["ingredient"] == "Vodka")
    assert data["abv"] == pytest.approx(100 * vodka_ml * 0.4 / 135, abs=0.1)
    assert data["std_drinks"] == pytest.approx(vodka_ml * 0.4 * 0.789 / 14, abs=0.01)
    with env[3]() as s:  # nothing was saved
        assert s.scalar(select(Ingredient).where(Ingredient.name == "Mystery Syrup")) is None


def test_admin_drink_list_states(env):
    client, b, _, sessions = env
    page = client.get("/admin/drinks").text
    assert "on the menu" in page and "needs Tequila" in page
    br = drink_ids(sessions, "Black Russian")[0]
    client.post(f"/admin/drink/{br}/toggle/enabled")
    assert "switched off" in client.get("/admin/drinks").text


def test_pump_cards_api(env):
    client, b, bus, sessions = env
    r = client.post("/api/dispenser/2", json={"ingredient": "tequila", "ticks_per_ml": 3.1})
    assert r.status_code == 200 and r.json()["ingredient"] == "Tequila" and r.json()["makes"] >= 1
    with sessions() as s:
        d = s.get(Dispenser, 2)
        assert (d.ingredient.name, d.ticks_per_ml) == ("Tequila", 3.1)
    assert client.post("/api/dispenser/2", json={"ingredient": "Nope"}).status_code == 400
    assert client.post("/api/dispenser/99", json={"ingredient": ""}).status_code == 400
    assert client.post("/api/dispenser/2", json={"ingredient": ""}).json()["ingredient"] == ""
    wait_idle(b)
    assert client.post("/api/pumps/run", json={"ms": 1, "reverse": True}).status_code == 202
    wait_idle(b)
    assert all(x.direction == 1 for x in bus.ports.values())  # forward again afterwards
    page = client.get("/admin").text
    assert "Empty line" in page and 'list="bottle-list"' in page
    client.post("/admin/on-hand", data={"add": "Orange Juice"})
    with sessions() as s:
        assert s.get(Ingredient, 4).on_hand


def test_admin_ingredient_edit(env):
    client, _, _, sessions = env
    client.post("/admin/ingredient/new", data={"name": "Tito's", "brand": "Tito's", "abv": "40",
                                               "alcoholic": "on", "generic_id": "1"})
    with sessions() as s:
        titos = s.scalar(select(Ingredient).where(Ingredient.name == "Tito's"))
        assert titos.generic.name == "Vodka" and titos.alcoholic
        titos_id = titos.id
    r = client.post("/admin/ingredient/1", data={"delete": "1"})  # vodka is used
    assert r.status_code == 400
    client.post(f"/admin/ingredient/{titos_id}", data={"delete": "1"})
    with sessions() as s:
        assert s.get(Ingredient, titos_id) is None


def test_admin_options(env):
    client, _, _, sessions = env
    client.post("/admin/options", data={"metric": "on", "show_taster": "on", "drink_size": "180",
                                         "shot_size": "lots"})
    with sessions() as s:
        assert options.get(s, "metric") is True
        assert options.get(s, "drink_size") == 180
        assert options.get(s, "shot_size") == 30       # bad value ignored
        assert options.get(s, "show_size") is False    # unticked box
    assert "Each shot is 30 ml" in client.get("/shots").text  # metric now


def test_hand_added_amounts_in_admin_and_plan(env):
    client, b, _, sessions = env
    client.post("/admin/recipes/load")
    with sessions() as s:
        manhattan = s.scalar(select(Drink).where(Drink.name == "Manhattan")).id
    form = client.get(f"/admin/drink/{manhattan}").text
    assert "<option selected>dash</option>" in form and "stir with ice" in form
    r = client.post(f"/admin/drink/{manhattan}", data={
        "name": "Manhattan", "enabled": "on", "finish": "Stir.", "size_ml": "50",
        **rows(("Whiskey", "50", "ml"), ("Vodka", "3", "dash"))}, follow_redirects=False)
    assert r.status_code == 303
    with sessions() as s:
        d = s.get(Drink, manhattan)
        assert d.finish == "Stir."
        assert [(i.ingredient_id, i.parts, i.hand_text) for i in d.items] == \
            [(7, 50, "50 ml"), (1, None, "3 dashes")]
    plan = client.get(f"/api/drink/{manhattan}/plan").json()
    # vodka is on pump #1, so the 3 dashes are pumped (2.7 ml)...
    assert {"dispenser": 1, "ingredient": "Vodka", "ml": 2.7} in plan["pumps"]
    # ...unless that's below min_pump_ml and it's on hand: then the guest adds them
    client.post("/admin/options", data={"min_pump_ml": "5", "show_size": "on"})
    client.post("/admin/on-hand", data={"listed": ["1"], "on_hand": ["1"]})
    plan = client.get(f"/api/drink/{manhattan}/plan").json()
    assert plan["after"] == [{"ingredient": "Vodka", "ml": None, "text": "3 dashes"}]
    assert plan["pumps"] == [{"dispenser": 15, "ingredient": "Whiskey", "ml": 50}]
    assert plan["finish"] == "Stir."


def drink_ids(sessions, *names):
    with sessions() as s:
        return [s.scalar(select(Drink.id).where(Drink.name == n)) for n in names]


def test_parties(env, tmp_path):
    client, b, _, sessions = env
    br, cc, sd = drink_ids(sessions, "Black Russian", "Cape Cod", "Screwdriver")
    assert client.get("/admin/parties").status_code == 200
    assert client.get("/admin/party/new").status_code == 200
    r = client.post("/admin/party/new", data={
        "name": "Birthday", "title": "Kevin's 40th", "welcome": "Tip your robot!",
        "color_page": "#102030", "color_button": "#fa6c19",  # the default: stored as ""
        "drink": [str(br), str(cc), str(sd)], "featured": [str(cc), str(br)],
        f"pos{cc}": "1", f"pos{br}": "2"},
        files={"logo": ("logo.png", b"\x89PNG fake", "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    with sessions() as s:
        party = s.scalar(select(Party))
        assert (party.title, party.color_page, party.color_button) == ("Kevin's 40th", "#102030", "")
        assert {(pd.drink_id, pd.featured) for pd in party.drinks} == {(br, True), (cc, True), (sd, False)}
        pid, logo = party.id, party.logo
    assert logo.endswith(".png") and client.get(f"/uploads/{logo}").content == b"\x89PNG fake"

    # not active yet: guests see everything, original look
    title = str(escape("Kevin's 40th"))  # as the page escapes it
    menu = client.get("/").text
    assert title not in menu and "--page: #102030" not in menu
    assert 'class="robot-bg' in menu  # no party logo: the robot backdrop
    # preview: the party's look and menu, remembered by a cookie
    menu = client.get(f"/?party={pid}").text
    assert title in menu and "Tip your robot!" in menu and "--page: #102030" in menu
    assert f"/uploads/{logo}" in menu and 'class="robot-bg' in menu  # banner on top, robot behind
    essentials = menu.split("the essentials")[1].split("the menu")[0]
    assert essentials.index("Cape Cod") < essentials.index("Black Russian")  # party order
    assert "Screwdriver" not in essentials  # on the list but not featured
    everything = client.get("/menu/all").text  # cookie keeps the preview
    assert "Screwdriver" in everything and "White Russian" not in everything
    client.get("/?party=0")
    assert "White Russian" in client.get("/menu/all").text

    # activate: everyone gets it; only one party active at a time
    client.post(f"/admin/party/{pid}/duplicate")
    with sessions() as s:
        copy_id = s.scalar(select(Party.id).where(Party.name == "Birthday (copy)"))
        assert len(s.get(Party, copy_id).drinks) == 3
    client.post(f"/admin/party/{pid}/activate")
    client.post(f"/admin/party/{copy_id}/activate")
    with sessions() as s:
        assert [p.id for p in s.scalars(select(Party).where(Party.active))] == [copy_id]
    assert "White Russian" not in client.get("/menu/all").text
    client.post(f"/admin/party/{copy_id}/deactivate")
    assert "White Russian" in client.get("/menu/all").text

    # bad logo; delete; deleting a drink drops it from party lists
    r = client.post(f"/admin/party/{pid}", data={"name": "Birthday"},
                    files={"logo": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    client.post(f"/admin/drink/{sd}", data={"delete": "1"})
    with sessions() as s:
        assert len(s.get(Party, copy_id).drinks) == 2
    client.post(f"/admin/party/{copy_id}/delete")
    with sessions() as s:
        assert s.get(Party, copy_id) is None


def test_theme_css():
    from bartendro.web.theme import darker, lighter, theme_css
    assert theme_css(None) == "" and theme_css(Party(color_page="", color_frame="", color_heading="",
                                                      color_button="", color_go="")) == ""
    css = theme_css(Party(color_page="#000000", color_frame="#ff0000", color_heading="nope",
                          color_button="#336699", color_go=""))
    assert "--page: #000000;" in css and "--frame-inner: " + darker("#ff0000") in css
    assert "--heading" not in css and "--btn-1: " + lighter("#336699") in css and "--go" not in css


def test_multi_spirit_drink_in_each_section_menu(env):
    client, b, _, sessions = env
    client.post("/admin/recipes/load")
    # (the old db's name for white rum is "Rum, Light"; loading the classics matched it)
    for n, name in [(2, "Tequila"), (3, "Rum, Light"), (4, "Gin"), (5, "Triple Sec"), (6, "Lemon Juice"),
                    (7, "Simple Syrup"), (8, "Cola")]:
        assert client.post(f"/api/dispenser/{n}", json={"ingredient": name}).status_code == 200
    menu = client.get("/").text
    for slug in ("vodka", "tequila", "rum", "gin"):
        assert f"/menu/{slug}" in menu
        assert "Long Island Iced Tea" in client.get(f"/menu/{slug}").text, slug
    assert client.get("/menu/all").text.count(">Long Island Iced Tea<") == 1
    admin = client.get("/admin/drinks").text
    row = admin[admin.index(">Long Island Iced Tea<"):].split("</tr>")[0]
    assert all(name in row for name in ("Vodka", "Tequila", "Rum", "Gin"))


def test_guest_names():
    from bartendro.web import guest_event
    event = {"type": "done", "name": "Margarita, Tommy's",
             "after": [["Bitters, Angostura", None, "2 dashes"]], "finish": ""}
    assert guest_event(event) == {"type": "done", "name": "Tommy's Margarita",
                                  "after": [["Angostura Bitters", None, "2 dashes"]], "finish": ""}
    assert guest_event({"state": "pouring", "pouring": "Martini, Dry"})["pouring"] == "Dry Martini"
    assert event["name"] == "Margarita, Tommy's"   # the bot's event isn't changed


def test_party_robot_and_led_sign(env):
    client, b, _, sessions = env
    r = client.post("/admin/party/new", data={"name": "Bar2D2", "robot": "bar2d2",
                                               "marquee": "  My Name is Bar2D2,\n I think I love you ;)  "},
                    follow_redirects=False)
    assert r.status_code == 303
    with sessions() as s:
        party = s.scalar(select(Party))
        assert (party.robot, party.marquee) == ("bar2d2", "My Name is Bar2D2, I think I love you ;)")
        pid = party.id
    assert 'name="robot"' in client.get(f"/admin/party/{pid}").text
    menu = client.get(f"/?party={pid}").text
    assert 'class="led-sign"' in menu and "I think I love you ;)" in menu and "/static/led-font.js" in menu
    assert 'aria-label="Bar2D2 robot"' in menu and "Bartendro robot" not in menu
    client.post(f"/admin/party/{pid}/duplicate")
    with sessions() as s:
        copy = s.scalars(select(Party).order_by(Party.id.desc())).first()
        assert (copy.robot, copy.marquee) == ("bar2d2", "My Name is Bar2D2, I think I love you ;)")
    # an unknown robot falls back to the party robot
    client.post(f"/admin/party/{pid}", data={"name": "Bar2D2", "robot": "hal9000", "marquee": ""})
    menu = client.get(f"/?party={pid}").text
    assert 'aria-label="Bartendro robot"' in menu and 'class="led-sign"' not in menu
    client.get("/?party=0")


def test_menu_cache_and_compression(env):
    client, b, _, sessions = env
    r = client.get("/menu/all", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    before = r.text.count('class="drink-item"')
    assert before > 0
    client.get("/menu/all")                       # served from the cache
    # emptying every pump is a database change: the menu is worked out again
    with sessions() as s:
        for d in s.scalars(select(Dispenser)):
            d.ingredient_id = None
        s.commit()
    assert client.get("/menu/all").text.count('class="drink-item"') < before
