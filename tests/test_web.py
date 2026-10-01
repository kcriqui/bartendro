import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from bartendro import bot as bot_mod
from bartendro.bot import Bot
from bartendro.db import open_db, options
from bartendro.db.importer import import_legacy
from bartendro.db.models import Dispenser, Drink, Ingredient
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
    for url in ["/", f"/drink/{d}", "/shots", "/admin", "/admin/drinks", f"/admin/drink/{d}",
                "/admin/drink/new", "/admin/ingredients", "/admin/ingredient/1", "/admin/ingredient/new",
                "/admin/options", "/admin/log", "/static/app.js", "/static/style.css"]:
        r = client.get(url)
        assert r.status_code == 200, url
    menu = client.get("/").text
    assert "Testbot" in menu and "Black Russian" in menu and "The essentials" in menu
    assert "Tequila Sunrise" not in menu  # no tequila on the dispensers
    assert 'class="on"' in client.get("/admin/drinks").text  # tab highlighted


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


def test_admin_drink_edit(env):
    client, b, _, sessions = env
    r = client.post("/admin/drink/new", data={
        "name": "Screwdriver Deluxe", "description": "OJ + vodka", "enabled": "on",
        "ingredient": ["1", "4", "", "1"], "parts": ["1", "2", "", "1"]}, follow_redirects=False)
    assert r.status_code == 303
    with sessions() as s:
        d = s.scalar(select(Drink).where(Drink.name == "Screwdriver Deluxe"))
        assert {(i.ingredient_id, i.parts) for i in d.items} == {(1, 2), (4, 2)}  # vodka rows added
        assert d.enabled and not d.popular
        drink_id = d.id
    assert "Screwdriver Deluxe" in client.get("/").text
    assert client.post(f"/admin/drink/{drink_id}/toggle/popular").json() == {"popular": True}
    client.post(f"/admin/drink/{drink_id}", data={"delete": "1"})
    with sessions() as s:
        assert s.get(Drink, drink_id) is None


def test_admin_ingredient_edit(env):
    client, _, _, sessions = env
    client.post("/admin/ingredient/new", data={"name": "Tito's", "brand": "Tito's", "abv": "40",
                                               "kind": "alcohol", "generic_id": "1"})
    with sessions() as s:
        titos = s.scalar(select(Ingredient).where(Ingredient.name == "Tito's"))
        assert titos.generic.name == "Vodka" and titos.kind.value == "alcohol"
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
    assert "Taster (30 ml)" in client.get(f"/drink/{black_russian(sessions)}").text  # metric now
