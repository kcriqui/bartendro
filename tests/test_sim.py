"""The /sim page: the simulated pumps, watched from a second window."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from bartendro import bot as bot_mod
from bartendro.bot import Bot
from bartendro.db import open_db
from bartendro.db.importer import import_legacy
from bartendro.db.models import Dispenser, Drink
from bartendro.hw.driver import Driver
from bartendro.hw.simulator import SimBus
from bartendro.web import create_app

DEFAULT_DB = Path(__file__).parent.parent / "ui" / "bartendro.db.default"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(SimBus, "time_scale", 1000.0)
    monkeypatch.setattr(bot_mod, "LED_DONE_SECONDS", 0.01)


def make(tmp_path, sim=True, **kw):
    sessions = open_db(tmp_path / "sim.db")
    with sessions() as s:
        import_legacy(DEFAULT_DB, s)
    bus = SimBus.with_dispensers(15)
    driver = Driver(bus.serial, bus.router)
    if sim:
        driver.sim = bus
    driver.discover()
    b = Bot(driver, sessions)
    b.start()
    return TestClient(create_app(b, "Testbot", **kw)), b, bus, sessions


def wait_idle(b, timeout=5):
    end = time.monotonic() + timeout
    while b.status()["busy"] and time.monotonic() < end:
        time.sleep(0.01)
    assert not b.status()["busy"]


def test_sim_page_and_link(tmp_path):
    client, *_ = make(tmp_path)
    with client:
        assert 'href="/sim"' in client.get("/").text
        page = client.get("/sim").text
        assert "simulated pumps" in page and 'data-controls="1"' in page
        with client.websocket_connect("/ws/sim") as ws:
            state = ws.receive_json()
        assert state["status"]["state"] == "ready"
        pumps = state["pumps"]
        assert [p["number"] for p in pumps] == list(range(1, 16))
        assert pumps[0]["led"] == "idle" and not pumps[0]["running"] and pumps[0]["ticks"] == 0
        assert any(p["bottle"] for p in pumps)


def test_no_sim_page_on_real_pumps(tmp_path):
    client, *_ = make(tmp_path, sim=False)
    with client:
        assert 'href="/sim"' not in client.get("/").text
        assert client.get("/sim").status_code == 404
        assert client.post("/api/sim/1/jam").status_code == 404


def test_view_only(tmp_path):
    client, *_ = make(tmp_path, sim_controls=False)
    with client:
        assert 'data-controls="0"' in client.get("/sim").text
        assert client.post("/api/sim/1/jam").status_code == 403
        assert client.post("/api/sim/1/level", json={"level": 50}).status_code == 403


def test_jam_stalls_and_reset_restarts(tmp_path):
    client, b, bus, sessions = make(tmp_path)
    with sessions() as s:
        drink = s.scalar(select(Drink).where(Drink.name == "Black Russian"))
        pumped = {r.ingredient_id for r in drink.items}
        jam = next(d.number for d in s.scalars(select(Dispenser)) if d.ingredient_id in pumped)
        drink_id = drink.id
    with client:
        assert client.post(f"/api/sim/{jam}/jam").json() == {"jammed": True}
        client.post(f"/api/drink/{drink_id}/make", json={})
        wait_idle(b)
        assert b.status()["state"] == "current_sense"
        stuck = bus.ports[jam - 1]
        assert stuck.over_current and stuck.led == "current_sense"
        client.post(f"/api/sim/{jam}/jam")  # unjam: the firmware still ignores commands...
        b.driver.led_idle()
        assert stuck.led == "current_sense"
        client.post("/api/reset")  # ...until the reset restarts it
        assert not stuck.over_current and stuck.led == "idle"
        assert b.status()["state"] == "ready"
        client.post(f"/api/drink/{drink_id}/make", json={})
        wait_idle(b)
        assert b.status()["state"] == "ready" and stuck.snapshot()["ticks"] > 0


def test_level(tmp_path):
    client, _b, bus, _ = make(tmp_path)
    with client:
        assert client.post("/api/sim/2/level", json={"level": 50}).json() == {"level": 50}
        assert bus.ports[1].level == 50
        assert client.post("/api/sim/2/level", json={"level": -1}).status_code == 422
        assert client.post("/api/sim/16/level", json={"level": 50}).status_code == 404
