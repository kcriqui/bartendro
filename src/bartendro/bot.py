"""The bot: state, one action at a time, and turning drinks into pump commands.
Port of ui/bartendro/mixer.py + fsm.py; works the same with real hardware or the simulator.

The web app (or anything else) calls make_drink / shot / clean / ...; those check what they
can up front (raising BusyError or CantPourError straight away) and then, with
background=True, do the pumping in a worker thread. Listeners get every state change and
pour event, from whichever thread caused it.

States (old fsm end states, plus the busy ones the old UI never showed):
    READY / LOW / OUT   levels fine / some bottle low / some bottle out (sensors on)
    HARD_OUT            nothing on the menu can be made
    POURING / CLEANING  busy
    CURRENT_SENSE       a pump stalled: check it, then reset()
    ERROR               hardware trouble: reset()
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import options
from .db.menu import makeable_drinks, scale_recipe
from .db.models import Dispenser, Drink, Ingredient, Level, PourLog
from .hw import protocol as p
from .hw.driver import MAX_DISPENSE_ML, DriverError, OverCurrentError

log = logging.getLogger(__name__)

# From ui/bartendro/mixer.py
LIQUID_OUT_THRESHOLD = 75
LIQUID_LOW_THRESHOLD = 120
CLEAN_SECONDS = 10
CLEAN_STAGGER = 0.15  # s between starting/stopping pumps (spreads the current draw)
CLEAN_LEFT = [4, 5, 6, 7, 8, 9, 10]          # dispenser indexes on a 15-pump bot
CLEAN_RIGHT = [0, 1, 2, 3, 11, 12, 13, 14]
LED_DONE_SECONDS = 5  # the "drink done" LED pattern shows this long, then back to idle
MIN_DRINK_ML = 10
LOCK_WAIT = 0.25  # s an action waits for the lock before reporting busy
RUN_PUMP_MAX_MS = 60_000  # longest manual pump run (priming a long line)


class State(str, enum.Enum):
    STARTING = "starting"
    READY = "ready"
    LOW = "low"
    OUT = "out"
    HARD_OUT = "hard_out"
    POURING = "pouring"
    CLEANING = "cleaning"
    CURRENT_SENSE = "current_sense"
    ERROR = "error"


RESTING = {State.READY, State.LOW, State.OUT, State.HARD_OUT}
STATUS_COLORS = {  # case status LED, as the old _state_* functions set it
    State.READY: (0, 1, 0), State.LOW: (1, 1, 0), State.OUT: (1, 0, 0),
    State.HARD_OUT: (1, 0, 0), State.ERROR: (1, 0, 0), State.CURRENT_SENSE: (1, 0, 0),
}


class BotError(Exception):
    pass


class BusyError(BotError):
    """Another drink (or cleaning, ...) is in progress."""


class CantPourError(BotError):
    """This request can't be done (missing ingredient, bottle out, bad size, ...)."""


@dataclass
class Plan:
    """What pouring one drink means: ml per dispenser number, and what to add by hand."""
    drink_id: int | None
    name: str
    size_ml: float
    pumps: dict[int, float]  # dispenser number -> ml
    manual: list[tuple[str, float]] = field(default_factory=list)  # (ingredient, ml) to add by hand

    @property
    def pumped_ml(self) -> float:
        return sum(self.pumps.values())


class Bot:
    def __init__(self, driver, sessions: sessionmaker[Session]):
        self.driver = driver
        self.sessions = sessions
        self.state = State.STARTING
        self.message = ""
        self.current: Plan | None = None  # what's being poured
        self._lock = threading.Lock()     # held for the whole of any action that moves pumps
        self._listeners: list = []
        self._led_timer: threading.Timer | None = None

    # ------------------------------------------------------------ status / events

    @property
    def dispenser_count(self) -> int:
        return self.driver.count()

    def status(self) -> dict:
        return {"state": self.state.value, "message": self.message,
                "busy": self._lock.locked(), "dispensers": self.dispenser_count,
                "pouring": self.current.name if self.current else None}

    def subscribe(self, fn) -> None:
        """fn(event: dict) is called on every change; event["type"] is "status" or a pour event."""
        self._listeners.append(fn)

    def unsubscribe(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _emit(self, event: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                log.exception("bot listener failed")

    def _set_state(self, state: State, message: str = "") -> None:
        self.state, self.message = state, message
        if state in STATUS_COLORS:
            self.driver.set_status_color(*STATUS_COLORS[state])
        log.info("state %s %s", state.value, message)
        self._emit({"type": "status", **self.status()})

    # ----------------------------------------------------------------- actions

    def start(self) -> None:
        """Check the levels and settle into a resting state (old EVENT_START)."""
        self._run(self._check, background=False, allow_broken=True)

    def reset(self) -> None:
        """Clear CURRENT_SENSE / ERROR (old EVENT_RESET)."""
        self._run(self._reset, background=False, allow_broken=True)

    def check_levels(self, background: bool = False) -> None:
        self._run(self._check, background=background, allow_broken=True)

    def plan_drink(self, drink_id: int, size_ml: float | None = None, strength: int = 0,
                   tartness: int = 0) -> Plan:
        with self.sessions() as s:
            drink = s.get(Drink, drink_id)
            if drink is None:
                raise CantPourError(f"no drink #{drink_id}")
            steps = int(options.get(s, "strength_steps"))
            if abs(strength) > steps or abs(tartness) > steps:
                raise CantPourError(f"strength and tartness go from -{steps} to {steps}")
            default = drink.size_ml or int(options.get(s, "drink_size"))
            size = float(size_ml or default)
            if not MIN_DRINK_ML <= size <= MAX_DISPENSE_ML:
                raise CantPourError(f"drink size must be {MIN_DRINK_ML}-{MAX_DISPENSE_ML} ml")
            amounts = scale_recipe(drink, size, strength, tartness, include_manual=True)
            if not amounts:
                raise CantPourError(f"{drink.name} has no ingredients")
            plan = Plan(drink.id, drink.name, size, {})
            for item in drink.items:
                ml = amounts[item.ingredient_id]
                if item.ingredient.manual:
                    plan.manual.append((item.ingredient.name, ml))
                    continue
                number = self._dispenser_for(s, item.ingredient)
                plan.pumps[number] = plan.pumps.get(number, 0) + ml
            return plan

    def make_drink(self, drink_id: int, size_ml: float | None = None, strength: int = 0,
                   tartness: int = 0, background: bool = False) -> Plan:
        plan = self.plan_drink(drink_id, size_ml, strength, tartness)
        self._run(self._pour, plan, background=background)
        return plan

    def shot(self, number: int, ml: float | None = None, background: bool = False) -> Plan:
        """A shot of whatever is in dispenser #number (old dispense_shot)."""
        with self.sessions() as s:
            d = self._dispenser(s, number)
            if d.ingredient is None:
                raise CantPourError(f"dispenser #{number} is empty")
            self._check_not_out(s, d)
            ml = float(ml or options.get(s, "shot_size"))
            plan = Plan(None, d.ingredient.name, ml, {number: ml})
            ingredient_id = d.ingredient_id
        if not 0 < ml <= MAX_DISPENSE_ML:
            raise CantPourError(f"shot size must be 1-{MAX_DISPENSE_ML} ml")
        self._run(self._pour, plan, ingredient_id, background=background)
        return plan

    def test_dispense(self, number: int, ml: float | None = None, background: bool = False) -> None:
        """Admin: pour ml (option test_dispense_ml) from one dispenser at full speed to check the
        calibration. Allowed in ERROR too, like the old fsm; not logged."""
        with self.sessions() as s:
            self._dispenser(s, number)
            ml = float(ml or options.get(s, "test_dispense_ml"))
        if not 0 < ml <= MAX_DISPENSE_ML:
            raise CantPourError(f"test amount must be 1-{MAX_DISPENSE_ML} ml")
        plan = Plan(None, f"test #{number}", ml, {number: ml})
        self._run(self._pour, plan, None, True, background=background, allow_broken=True)

    def run_pump(self, number: int, ms: int, reverse: bool = False, background: bool = False) -> None:
        """Admin: run one pump for ms milliseconds, e.g. to prime a line or (reverse) empty it."""
        with self.sessions() as s:
            self._dispenser(s, number)
        if not 0 < ms <= RUN_PUMP_MAX_MS:
            raise CantPourError(f"run time must be 1-{RUN_PUMP_MAX_MS} ms")
        self._run(self._run_pump, number - 1, ms, reverse, background=background, allow_broken=True)

    def clean(self, which: str = "all", background: bool = False) -> None:
        """Run the pumps for 10 s to flush the lines (old CleanCycle). `which`: all/left/right;
        left and right only mean something on a 15-pump bot."""
        if which not in ("all", "left", "right"):
            raise CantPourError("clean: all, left or right")
        self._run(self._clean, which, background=background, allow_broken=True)

    # ------------------------------------------------------------ internals

    def _run(self, fn, *args, background: bool, allow_broken: bool = False) -> None:
        """Take the lock (or raise BusyError), then run fn here or in a worker thread. Errors
        while pumping become the CURRENT_SENSE / ERROR state instead of exceptions."""
        # Short wait: the LED timer holds the lock for a few ms; a real action holds it far longer.
        if not self._lock.acquire(timeout=LOCK_WAIT):
            raise BusyError("Bartendro is busy - try again in a moment")
        if not allow_broken and self.state in (State.ERROR, State.CURRENT_SENSE):
            self._lock.release()
            raise CantPourError("Bartendro needs a reset first: " + (self.message or self.state.value))

        def work():
            self._cancel_led_timer()
            try:
                fn(*args)
            except OverCurrentError as e:
                self._safe_led_idle()
                self._set_state(State.CURRENT_SENSE, str(e))
            except (DriverError, OSError) as e:
                log.exception("hardware error")
                self._safe_led_idle()
                self._set_state(State.ERROR, str(e))
            except Exception as e:  # never leave the bot stuck busy
                log.exception("unexpected error")
                self._set_state(State.ERROR, f"internal error: {e}")
            finally:
                self.current = None
                self._lock.release()

        if background:
            threading.Thread(target=work, name=fn.__name__, daemon=True).start()
        else:
            work()

    def _dispenser(self, s: Session, number: int) -> Dispenser:
        if not 1 <= number <= self.dispenser_count:
            raise CantPourError(f"no dispenser #{number} (found {self.dispenser_count})")
        d = s.get(Dispenser, number)
        if d is None:
            d = Dispenser(number=number)
            s.add(d)
            s.flush()
        return d

    def _check_not_out(self, s: Session, d: Dispenser) -> None:
        if options.get(s, "use_liquid_level_sensors") and d.level is Level.OUT:
            raise CantPourError(f"dispenser #{d.number} ({d.ingredient.name}) is out")

    def _dispenser_for(self, s: Session, ingredient: Ingredient) -> int:
        """The dispenser to pour `ingredient` from: one holding exactly it, else one holding a
        brand of it (Tito's for Vodka); lowest number first; skipping bottles that are out."""
        sensors = options.get(s, "use_liquid_level_sensors")
        candidates = []
        for d in s.scalars(select(Dispenser).where(Dispenser.number <= self.dispenser_count,
                                                   Dispenser.ingredient_id.is_not(None))):
            depth, ing = 0, d.ingredient
            while ing is not None and ing.id != ingredient.id and depth < 10:
                ing, depth = ing.generic, depth + 1
            if ing is not None and ing.id == ingredient.id:
                candidates.append((depth, d.number, d))
        if not candidates:
            raise CantPourError(f"no dispenser has {ingredient.name}")
        usable = [c for c in sorted(candidates) if not (sensors and c[2].level is Level.OUT)]
        if not usable:
            raise CantPourError(f"{ingredient.name} is out")
        return usable[0][1]

    def _pour(self, plan: Plan, shot_ingredient: int | None = None, test: bool = False) -> None:
        self.current = plan
        self._set_state(State.POURING, plan.name)
        self._emit({"type": "pouring", "name": plan.name, "ml": round(plan.pumped_ml),
                    "manual": [[n, round(ml)] for n, ml in plan.manual]})
        with self.sessions() as s:
            cal = {n - 1: d.ticks_per_ml for n in plan.pumps
                   if (d := s.get(Dispenser, n)) is not None and d.ticks_per_ml}
        self.driver.led_dispense()
        t0 = time.monotonic()
        self.driver.pour_ml({n - 1: ml for n, ml in plan.pumps.items()}, always_fast=test,
                            ticks_per_ml=cal)
        log.info("poured %s: %s in %.1f s", plan.name,
                 ", ".join(f"#{n} {ml:.0f} ml" for n, ml in sorted(plan.pumps.items())),
                 time.monotonic() - t0)
        self.driver.led_complete()
        self._led_timer = threading.Timer(LED_DONE_SECONDS, self._led_idle_if_free)
        self._led_timer.daemon = True
        self._led_timer.start()
        if not test:
            with self.sessions() as s:
                s.add(PourLog(drink_id=plan.drink_id, ingredient_id=shot_ingredient,
                              size_ml=plan.pumped_ml))
                s.commit()
        self._emit({"type": "done", "name": plan.name,
                    "manual": [[n, round(ml)] for n, ml in plan.manual]})
        self._check()

    def _reset(self) -> None:
        self.driver.led_idle()
        self._check()

    def _safe_led_idle(self) -> None:
        try:
            self.driver.led_idle()
        except Exception:
            log.exception("could not reset the dispenser LEDs")

    def _led_idle_if_free(self) -> None:
        if self._lock.acquire(blocking=False):
            try:
                self.driver.led_idle()
            finally:
                self._lock.release()

    def _cancel_led_timer(self) -> None:
        if self._led_timer:
            self._led_timer.cancel()
            self._led_timer = None

    def _run_pump(self, index: int, ms: int, reverse: bool) -> None:
        self._set_state(State.POURING, f"running pump #{index + 1}")
        if reverse:
            self.driver.set_motor_direction(index, p.MOTOR_DIRECTION_BACKWARD)
        try:
            if not self.driver.dispense_time(index, ms):
                raise DriverError(f"pump #{index + 1} didn't accept the run command")
            time.sleep(ms / 1000 + 0.2)
        finally:
            if reverse:
                self.driver.set_motor_direction(index, p.MOTOR_DIRECTION_FORWARD)
        self._check()

    def _clean(self, which: str) -> None:
        self._set_state(State.CLEANING, f"cleaning ({which})")
        n = self.dispenser_count
        if n == 15 and which != "all":
            pumps = CLEAN_LEFT if which == "left" else CLEAN_RIGHT
        else:
            pumps = list(range(n))
        self.driver.led_clean()
        started = []
        try:
            for i in pumps:
                self.driver.set_motor_direction(i, p.MOTOR_DIRECTION_FORWARD)
                self.driver.start(i)
                started.append(i)
                time.sleep(CLEAN_STAGGER)
            time.sleep(CLEAN_SECONDS)
        finally:
            for i in started:
                self.driver.stop(i)
                time.sleep(CLEAN_STAGGER)
        time.sleep(0.1)
        self.driver.led_idle()
        self._check()

    def _check(self) -> None:
        """Read the levels (sensors on) and pick the resting state (old _state_check)."""
        with self.sessions() as s:
            worst = Level.OK
            if options.get(s, "use_liquid_level_sensors"):
                worst = self._read_levels(s)
            s.commit()
            can_make = makeable_drinks(s, dispenser_count=self.dispenser_count)
        if not can_make:
            self.driver.led_idle()
            self._set_state(State.HARD_OUT, "nothing on the menu can be made")
        elif worst is Level.OUT:
            self.driver.led_idle()
            self._set_state(State.OUT, "a bottle is empty")
        elif worst is Level.LOW:
            self.driver.led_idle()
            self._set_state(State.LOW, "a bottle is running low")
        else:
            self._set_state(State.READY)

    def _read_levels(self, s: Session) -> Level:
        if not self.driver.update_liquid_levels():
            raise DriverError("failed to update liquid levels")
        time.sleep(0.01)
        worst = Level.OK
        for i in range(self.dispenser_count):
            level = self.driver.get_liquid_level(i)
            if level < 0:
                raise DriverError(f"failed to read the liquid level of dispenser #{i + 1}")
            d = self._dispenser(s, i + 1)
            if level <= LIQUID_OUT_THRESHOLD:
                d.level = worst = Level.OUT
            elif level <= LIQUID_LOW_THRESHOLD:
                d.level = Level.LOW
                if worst is Level.OK:
                    worst = Level.LOW
            else:
                d.level = Level.OK
        return worst
