"""bartendro-hw: test the dispenser chain from the command line.

Examples (on the Pi):
    bartendro-hw discover
    bartendro-hw flash
    bartendro-hw pour 3 30          # 30 ml from dispenser #3
    bartendro-hw level
    bartendro-hw --sim 15 discover  # no hardware needed
Dispenser numbers are 1-based, in the order they were found (normally port order).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import __version__
from .hw import protocol as p
from .hw.connect import ConnectError, open_driver
from .hw.driver import DriverError
from .hw.router import RouterError

LIQUID_OUT_THRESHOLD = 75  # from ui/bartendro/mixer.py
LIQUID_LOW_THRESHOLD = 120


def _index(driver, n: int) -> int:
    if not 1 <= n <= driver.count():
        raise SystemExit(f"no dispenser #{n}: found {driver.count()}")
    return n - 1


def _confirm(args, msg: str) -> bool:
    if args.yes:
        return True
    try:
        return input(f"{msg} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_discover(driver, args) -> int:
    if args.verbose:
        for line in driver.startup_log:
            print("  " + line)
    print(f"Found {driver.count()} dispenser(s), firmware version {driver.version}")
    for d in driver.dispensers:
        ok = "ok" if driver.ping(d.index) else "NO PING"
        print(f"  #{d.index + 1:<3} port {d.port:<3} id {d.id:3d}  {ok}")
    for dup, ports in driver.id_conflicts:
        print(f"  ID CONFLICT: id {dup} on ports {ports} (ignored; reprogram one of them)")
    return 0 if driver.count() else 1


def cmd_led(driver, args) -> int:
    fn = {"idle": driver.led_idle, "dispense": driver.led_dispense, "done": driver.led_complete,
          "clean": driver.led_clean, "off": driver.led_off}[args.pattern]
    return 0 if fn() else 1


def cmd_flash(driver, args) -> int:
    """Run the LED patterns so you can see every dispenser is listening."""
    for name, fn in (("dispense", driver.led_dispense), ("done", driver.led_complete),
                     ("clean", driver.led_clean)):
        print(f"LEDs: {name}")
        fn()
        time.sleep(args.seconds)
    driver.led_idle()
    print("LEDs: idle")
    return 0


def cmd_pour(driver, args) -> int:
    i = _index(driver, args.dispenser)
    if not _confirm(args, f"Pour {args.ml:g} ml from dispenser #{args.dispenser}? Put a glass under it."):
        print("cancelled")
        return 1
    driver.led_dispense()
    t0 = time.monotonic()
    try:
        driver.pour_ml({i: args.ml}, always_fast=args.fast)
    finally:
        driver.led_complete()
    print(f"poured {args.ml:g} ml from #{args.dispenser} in {time.monotonic() - t0:.1f} s")
    time.sleep(1)
    driver.led_idle()
    return 0


def cmd_run(driver, args) -> int:
    """Run a pump for a time, e.g. to prime a line or (with --reverse) empty it."""
    i = _index(driver, args.dispenser)
    direction = "backward" if args.reverse else "forward"
    if not _confirm(args, f"Run dispenser #{args.dispenser} {direction} for {args.ms} ms?"):
        print("cancelled")
        return 1
    driver.set_motor_direction(i, p.MOTOR_DIRECTION_BACKWARD if args.reverse else p.MOTOR_DIRECTION_FORWARD)
    ok = driver.dispense_time(i, args.ms)
    time.sleep(args.ms / 1000 + 0.2)
    driver.set_motor_direction(i, p.MOTOR_DIRECTION_FORWARD)
    return 0 if ok else 1


def cmd_level(driver, args) -> int:
    if not driver.update_liquid_levels():
        print("update liquid levels failed")
        return 1
    time.sleep(0.01)
    targets = [_index(driver, args.dispenser)] if args.dispenser else range(driver.count())
    rc = 0
    for i in targets:
        level = driver.get_liquid_level(i)
        if level < 0:
            state, rc = "READ FAILED", 1
        elif level <= LIQUID_OUT_THRESHOLD:
            state = "out"
        elif level <= LIQUID_LOW_THRESHOLD:
            state = "low"
        else:
            state = "ok"
        print(f"  #{i + 1:<3} level {level:5d}  {state}")
    return rc


def cmd_info(driver, args) -> int:
    targets = [_index(driver, args.dispenser)] if args.dispenser else range(driver.count())
    for i in targets:
        d = driver.dispensers[i]
        low, out = driver.get_liquid_level_thresholds(i)
        print(f"  #{i + 1:<3} port {d.port:<3} id {d.id:3d}  version {driver.get_version(i)}  "
              f"saved ticks {driver.get_saved_tick_count(i)}  level thresholds low {low} / out {out}")
    return 0


def cmd_status_led(driver, args) -> int:
    if not driver.status_led:
        print("status LED not available (gpiozero missing or no GPIO)")
        return 1
    rgb = {"red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1), "white": (1, 1, 1), "off": (0, 0, 0)}
    driver.set_status_color(*rgb[args.color])
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bartendro-hw", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--device", help="serial device (default: /dev/serial0, ttyAMA0, ttyS0)")
    ap.add_argument("--i2c-bus", type=int, default=1, help="I2C bus of the router board (default 1)")
    ap.add_argument("--sim", type=int, metavar="N", default=0, help="simulate N dispensers, no hardware")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-y", "--yes", action="store_true", help="don't ask before running pumps")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="find dispensers and ping each").set_defaults(fn=cmd_discover)

    s = sub.add_parser("led", help="set an LED pattern on all dispensers")
    s.add_argument("pattern", choices=["idle", "dispense", "done", "clean", "off"])
    s.set_defaults(fn=cmd_led)

    s = sub.add_parser("flash", help="cycle the LED patterns on all dispensers")
    s.add_argument("--seconds", type=float, default=2.0)
    s.set_defaults(fn=cmd_flash)

    s = sub.add_parser("pour", help="pour ml from one dispenser")
    s.add_argument("dispenser", type=int)
    s.add_argument("ml", type=float)
    s.add_argument("--fast", action="store_true", help="full speed even under 20 ml")
    s.set_defaults(fn=cmd_pour)

    s = sub.add_parser("run", help="run a pump for a time (prime / empty a line)")
    s.add_argument("dispenser", type=int)
    s.add_argument("ms", type=int)
    s.add_argument("--reverse", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("level", help="read liquid level sensors")
    s.add_argument("dispenser", type=int, nargs="?")
    s.set_defaults(fn=cmd_level)

    s = sub.add_parser("info", help="firmware version, tick count, level thresholds")
    s.add_argument("dispenser", type=int, nargs="?")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("status-led", help="set the case status LED")
    s.add_argument("color", choices=["red", "green", "blue", "white", "off"])
    s.set_defaults(fn=cmd_status_led)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        with open_driver(device=args.device, i2c_bus=args.i2c_bus, simulate=args.sim) as driver:
            if args.sim:
                driver.sim.time_scale = 1.0
            return args.fn(driver, args)
    except (ConnectError, RouterError, DriverError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
