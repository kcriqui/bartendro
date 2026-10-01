import threading
from pathlib import Path

import pytest
from sqlalchemy import select

from bartendro import bot as bot_mod
from bartendro.bot import Bot, BusyError, CantPourError, State
from bartendro.db import open_db, options
from bartendro.db.importer import import_legacy
from bartendro.db.models import Dispenser, Drink, Ingredient, Kind, PourLog, RecipeItem
from bartendro.hw import protocol as p
from bartendro.hw.driver import TICKS_PER_ML, Driver
from bartendro.hw.simulator import SimBus, SimDispenser

DEFAULT_DB = Path(__file__).parent.parent / "ui" / "bartendro.db.default"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(SimBus, "time_scale", 1000.0)
    monkeypatch.setattr(bot_mod, "CLEAN_SECONDS", 0)
    monkeypatch.setattr(bot_mod, "CLEAN_STAGGER", 0)
    monkeypatch.setattr(bot_mod, "LED_DONE_SECONDS", 0.01)


@pytest.fixture
def sessions(tmp_path):
    Session = open_db(tmp_path / "bot.db")
    with Session() as s:
        import_legacy(DEFAULT_DB, s)  # 15 dispensers: #1 Vodka, #2 Kahlua, #3 Baileys, ...
    return Session


def make_bot(sessions, bus=None):
    bus = bus or SimBus.with_dispensers(15)
    driver = Driver(bus.serial, bus.router)
    driver.discover()
    b = Bot(driver, sessions)
    b.events = []
    b.subscribe(b.events.append)
    b.start()
    return b, bus


def drink_id(sessions, name):
    with sessions() as s:
        return s.scalar(select(Drink.id).where(Drink.name == name))


def test_starts_ready(sessions):
    b, _ = make_bot(sessions)
    assert b.state is State.READY
    assert b.status() == {"state": "ready", "message": "", "busy": False, "dispensers": 15, "pouring": None}


def test_make_drink(sessions):
    b, bus = make_bot(sessions)
    plan = b.make_drink(drink_id(sessions, "Black Russian"))
    assert set(plan.pumps) == {1, 2} and plan.pumped_ml == pytest.approx(150)  # option drink_size
    for n, ml in plan.pumps.items():
        assert bus.ports[n - 1].poured_ticks == int(ml * TICKS_PER_ML)
    assert sum(x.poured_ticks for x in bus.ports.values()) == sum(int(ml * TICKS_PER_ML) for ml in plan.pumps.values())
    assert b.state is State.READY
    assert [e["type"] for e in b.events][-4:] == ["status", "pouring", "done", "status"]
    with sessions() as s:
        log = s.scalars(select(PourLog)).one()
        assert (log.drink_id, round(log.size_ml)) == (plan.drink_id, 150)


def test_size_strength_and_validation(sessions):
    b, _ = make_bot(sessions)
    d = drink_id(sessions, "Black Russian")  # vodka 2 parts? whatever the recipe: both alcohol
    assert b.plan_drink(d, size_ml=60).pumped_ml == pytest.approx(60)
    with pytest.raises(CantPourError, match="size"):
        b.plan_drink(d, size_ml=5000)
    with pytest.raises(CantPourError, match="-2 to 2"):
        b.plan_drink(d, strength=3)
    with pytest.raises(CantPourError, match="no drink"):
        b.plan_drink(9999)


def test_missing_ingredient(sessions):
    b, _ = make_bot(sessions)
    with sessions() as s:  # a drink that needs something no dispenser has
        tequila = s.scalar(select(Ingredient).where(Ingredient.name == "Tequila"))
        s.add(Drink(id=500, name="Tequila Shot", items=[RecipeItem(ingredient=tequila, parts=1)]))
        s.commit()
    with pytest.raises(CantPourError, match="no dispenser has Tequila"):
        b.make_drink(500)
    assert b.state is State.READY


def test_brand_on_dispenser_pours_generic_recipe(sessions):
    b, bus = make_bot(sessions)
    with sessions() as s:
        vodka = s.get(Ingredient, 1)
        titos = Ingredient(name="Tito's", kind=Kind.ALCOHOL, generic=vodka)
        s.add(titos)
        s.flush()
        s.get(Dispenser, 1).ingredient = titos  # #1 now holds Tito's, recipes still say Vodka
        s.commit()
    plan = b.make_drink(drink_id(sessions, "Black Russian"))
    assert set(plan.pumps) == {1, 2}


def test_manual_ingredients_are_listed_not_pumped(sessions):
    b, _ = make_bot(sessions)
    with sessions() as s:
        mint = Ingredient(name="Mint", manual=True)
        vodka = s.get(Ingredient, 1)
        s.add(Drink(id=501, name="Minty", items=[RecipeItem(ingredient=vodka, parts=3, position=0),
                                                 RecipeItem(ingredient=mint, parts=1, position=1)]))
        s.commit()
    plan = b.make_drink(501, size_ml=100)
    assert plan.pumps == pytest.approx({1: 75}) and plan.manual == [("Mint", 25)]
    assert b.events[-2]["manual"] == [["Mint", 25]]


def test_busy(sessions, monkeypatch):
    b, _ = make_bot(sessions)
    gate = threading.Event()
    release = threading.Event()
    real = b.driver.pour_ml

    def slow_pour(*a, **kw):
        gate.set()
        release.wait(5)
        return real(*a, **kw)
    monkeypatch.setattr(b.driver, "pour_ml", slow_pour)
    d = drink_id(sessions, "Black Russian")
    b.make_drink(d, background=True)
    assert gate.wait(5)
    assert b.status()["busy"] and b.state is State.POURING and b.status()["pouring"] == "Black Russian"
    with pytest.raises(BusyError):
        b.make_drink(d)
    with pytest.raises(BusyError):
        b.clean()
    release.set()
    for _ in range(500):
        if not b.status()["busy"]:
            break
        threading.Event().wait(0.01)
    assert b.state is State.READY and not b.status()["busy"]


def test_stalled_pump_needs_reset(sessions):
    b, bus = make_bot(sessions)
    bus.ports[1].over_current = True
    d = drink_id(sessions, "Black Russian")
    b.make_drink(d)
    assert b.state is State.CURRENT_SENSE and "stalled" in b.message
    with pytest.raises(CantPourError, match="reset"):
        b.make_drink(d)
    bus.ports[1].over_current = False
    b.reset()
    assert b.state is State.READY
    b.make_drink(d)
    assert b.state is State.READY


def test_hardware_error_becomes_error_state(sessions, monkeypatch):
    b, _ = make_bot(sessions)
    monkeypatch.setattr(b.driver, "dispense_ticks", lambda *a, **kw: False)
    b.make_drink(drink_id(sessions, "Black Russian"))
    assert b.state is State.ERROR and "failed" in b.message
    assert not b.status()["busy"]


def test_liquid_levels(sessions):
    with sessions() as s:
        options.set(s, "use_liquid_level_sensors", True)
        s.commit()
    bus = SimBus({i: SimDispenser(i + 1, level=200) for i in range(15)})
    bus.ports[1].level = 100   # Kahlua low
    b, _ = make_bot(sessions, bus)
    assert b.state is State.LOW
    bus.ports[0].level = 50    # Vodka out
    b.check_levels()
    assert b.state is State.OUT
    with sessions() as s:
        assert s.get(Dispenser, 1).level.value == "out"
    with pytest.raises(CantPourError, match="Vodka is out"):
        b.make_drink(drink_id(sessions, "Black Russian"))
    with pytest.raises(CantPourError, match="is out"):
        b.shot(1)
    for d in bus.ports.values():
        d.level = 10
    b.check_levels()
    assert b.state is State.HARD_OUT


def test_shot_and_test_dispense(sessions):
    b, bus = make_bot(sessions)
    plan = b.shot(3)
    assert plan.name == "Baileys" and bus.ports[2].poured_ticks == int(30 * TICKS_PER_ML)
    b.test_dispense(4, 10)
    assert bus.ports[3].poured_ticks == int(10 * TICKS_PER_ML)
    with sessions() as s:
        logs = s.scalars(select(PourLog)).all()
        assert [(l.ingredient_id, l.size_ml) for l in logs] == [(plan_ing(s, 3), 30)]  # test not logged
    with pytest.raises(CantPourError, match="no dispenser #16"):
        b.shot(16)


def plan_ing(s, number):
    return s.get(Dispenser, number).ingredient_id


def test_calibration_per_dispenser(sessions):
    b, bus = make_bot(sessions)
    with sessions() as s:
        s.get(Dispenser, 3).ticks_per_ml = 3.0
        s.commit()
    b.shot(3, 20)
    assert bus.ports[2].poured_ticks == 60


def test_run_pump_and_clean(sessions, monkeypatch):
    b, bus = make_bot(sessions)
    b.run_pump(5, 1, reverse=True)
    assert bus.ports[4].direction == p.MOTOR_DIRECTION_FORWARD  # set back after running in reverse
    started = []
    real_start = b.driver.start
    monkeypatch.setattr(b.driver, "start", lambda i: started.append(i) or real_start(i))
    b.clean("left")
    assert started == bot_mod.CLEAN_LEFT
    assert not any(x.dispensing for x in bus.ports.values())
    assert b.state is State.READY


def test_small_bot_cleans_all_pumps(sessions, monkeypatch):
    bus = SimBus.with_dispensers(3)
    b, _ = make_bot(sessions, bus)
    started = []
    real_start = b.driver.start
    monkeypatch.setattr(b.driver, "start", lambda i: started.append(i) or real_start(i))
    b.clean("left")  # no left/right on a 3-pump bot
    assert started == [0, 1, 2]
    assert b.dispenser_count == 3
    with pytest.raises(CantPourError, match="no dispenser #4"):
        b.shot(4)
