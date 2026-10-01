import pytest

from bartendro.cli import main
from bartendro.hw import protocol as p
from bartendro.hw.driver import TICKS_PER_ML, Driver, OverCurrentError
from bartendro.hw.simulator import SimBus, SimDispenser


@pytest.fixture(autouse=True)
def fast_pumps(monkeypatch):
    monkeypatch.setattr(SimBus, "time_scale", 1000.0)


def make(bus):
    d = Driver(bus.serial, bus.router)
    d.discover()
    return d


def test_discovers_full_bot():
    bus = SimBus.with_dispensers(15)
    d = make(bus)
    assert d.count() == 15
    assert [x.port for x in d.dispensers] == list(range(15))
    assert d.version == 3
    assert all(x.led == "idle" for x in bus.ports.values())
    assert all(d.ping(i) for i in range(15))


def test_discovers_small_bot_with_gaps():
    # 3-pump bot plugged into ports 0, 1 and 5
    bus = SimBus({0: SimDispenser(10), 1: SimDispenser(11), 5: SimDispenser(12)})
    d = make(bus)
    assert [(x.index, x.port, x.id) for x in d.dispensers] == [(0, 0, 10), (1, 1, 11), (2, 5, 12)]


def test_id_zero_is_ignored_and_duplicates_are_dropped():
    bus = SimBus({0: SimDispenser(0), 1: SimDispenser(7), 2: SimDispenser(7), 3: SimDispenser(9)})
    d = make(bus)
    assert [x.id for x in d.dispensers] == [9]
    assert d.id_conflicts == [(7, [1, 2])]
    assert bus.ports[1].id_conflict or bus.ports[2].id_conflict


def test_v2_firmware_has_no_version_reply():
    bus = SimBus.with_dispensers(2, version=2)
    assert make(bus).version == 2


def test_pour_sends_ticks_and_waits():
    bus = SimBus.with_dispensers(3)
    d = make(bus)
    d.pour_ml({0: 30, 2: 10})
    assert bus.ports[0].poured_ticks == int(30 * TICKS_PER_ML)
    assert bus.ports[2].poured_ticks == int(10 * TICKS_PER_ML)
    assert bus.ports[1].poured_ticks == 0
    assert not any(x.dispensing for x in bus.ports.values())


def test_over_current_raises():
    bus = SimBus.with_dispensers(1)
    d = make(bus)
    bus.ports[0].over_current = True
    with pytest.raises(OverCurrentError):
        d.pour_ml({0: 30})


def test_liquid_levels_and_thresholds():
    bus = SimBus({0: SimDispenser(1, level=200), 1: SimDispenser(2, level=80)})
    d = make(bus)
    assert d.update_liquid_levels()
    assert [d.get_liquid_level(i) for i in range(2)] == [200, 80]
    assert d.set_liquid_level_thresholds(1, 130, 70)
    assert d.get_liquid_level_thresholds(1) == (130, 70)


def test_led_broadcast_reaches_all():
    bus = SimBus.with_dispensers(4)
    d = make(bus)
    d.led_dispense()
    assert {x.led for x in bus.ports.values()} == {"dispense"}
    assert bus.sync_on


def test_cli_simulated(capsys):
    assert main(["--sim", "3", "discover"]) == 0
    assert "Found 3 dispenser(s)" in capsys.readouterr().out
    assert main(["--sim", "3", "--yes", "pour", "2", "5"]) == 0
    assert main(["--sim", "3", "level"]) == 0
    assert main(["--sim", "3", "info", "1"]) == 0
