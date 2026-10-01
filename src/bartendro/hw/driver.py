"""Talks to the dispensers through the router board. Python 3 port of
ui/bartendro/router/driver.py (RouterDriver); behaviour kept the same unless noted.

Dispensers are addressed by *index* (0 .. count-1, in the order they were found).
The driver maps an index to the router port it is plugged into and to the dispenser's
own id (stored in its EEPROM, used in every packet).
"""

from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass

from . import protocol as p

log = logging.getLogger(__name__)

MAX_DISPENSERS = 15
DEFAULT_TIMEOUT = 2.0  # seconds
DISPENSER_DEFAULT_VERSION = 2  # v2 firmware does not answer GET_VERSION

# From ui/bartendro/mixer.py
TICKS_PER_ML = 2.78
FULL_SPEED = 255
HALF_SPEED = 166
SLOW_DISPENSE_THRESHOLD = 20  # ml: smaller amounts are poured at half speed
MAX_DISPENSE_ML = 1000

DISCOVERY_RETRIES = 5  # the old code retried forever on garbled id replies


class DriverError(Exception):
    pass


class OverCurrentError(DriverError):
    """A pump stalled (current sense tripped)."""


@dataclass
class Dispenser:
    index: int  # 0-based, order found
    port: int  # router port 0-14
    id: int  # id stored in the dispenser


class Driver:
    """`serial` is a pyserial-like object (read/write/reset_input_buffer/reset_output_buffer/
    timeout attribute); `router` is a RouterSelect (or the simulator's stand-in);
    `status_led` is optional."""

    def __init__(self, serial, router, status_led=None):
        self.ser = serial
        self.router = router
        self.status_led = status_led
        self.dispensers: list[Dispenser] = []
        self.version = DISPENSER_DEFAULT_VERSION
        self.startup_log: list[str] = []
        self.id_conflicts: list[tuple[int, list[int]]] = []  # (id, ports)

    # ------------------------------------------------------------------ setup

    def count(self) -> int:
        return len(self.dispensers)

    def _log_startup(self, txt: str) -> None:
        log.info(txt)
        self.startup_log.append(txt)

    def discover(self) -> list[Dispenser]:
        """Reset the router and find the dispensers on every port."""
        self.startup_log = []
        self.dispensers = []
        self.id_conflicts = []
        self.set_status_color(0, 0, 1)
        self.router.reset()

        # Prime the line.
        self.ser.write(bytes([170, 170, 170]))
        time.sleep(0.001)
        self.ser.timeout = 0.01

        for port in range(MAX_DISPENSERS):
            self.router.select(port)
            time.sleep(0.01)
            for attempt in range(DISCOVERY_RETRIES):
                self.ser.reset_input_buffer()
                self.ser.write(b"???")  # each '?' makes the dispenser send its id
                data = self.ser.read(3)
                hexs = " ".join(f"{b:02X}" for b in data)
                if len(data) == 3:
                    if not (data[0] == data[1] == data[2]):
                        self._log_startup(f"port {port}: {hexs} -- inconsistent, retrying")
                        continue
                    if data[0] == 0:
                        self._log_startup(f"port {port}: ignoring dispenser id 0")
                        break
                    d = Dispenser(index=len(self.dispensers), port=port, id=data[0])
                    self.dispensers.append(d)
                    self._log_startup(f"port {port}: found dispenser id {d.id:02X} -> #{d.index + 1}")
                    break
                elif len(data) > 1:
                    self._log_startup(f"port {port}: {hexs} -- short reply, retrying")
                    time.sleep(0.5)
                else:
                    break  # nothing on this port
            else:
                self._log_startup(f"port {port}: no consistent reply after {DISCOVERY_RETRIES} tries")

        # 0xFF ends the id phase: dispensers switch to packet mode.
        if self.dispensers:
            self.router.select(self.dispensers[0].port)
        self.ser.timeout = DEFAULT_TIMEOUT
        self.ser.write(b"\xff")

        self._handle_id_conflicts()

        if self.dispensers:
            v = self.get_version(0)
            if v < 0:
                self.version = DISPENSER_DEFAULT_VERSION
            else:
                self.version = v
                if self.status_led:
                    self.status_led.swap_blue_green()
            log.info("dispenser firmware version %d (checked dispenser #1 only)", self.version)
            self.led_idle()
        return self.dispensers

    def _handle_id_conflicts(self) -> None:
        """Two dispensers with the same id can't be told apart: flag them (their LEDs
        show the conflict) and drop them, like the old driver did."""
        counts = collections.Counter(d.id for d in self.dispensers)
        dups = [i for i, n in counts.items() if n > 1]
        if not dups:
            return
        for dup in dups:
            same = [d for d in self.dispensers if d.id == dup]
            self._log_startup(f"ERROR: dispenser id {dup:02X} on ports {[d.port for d in same]}")
            self.id_conflicts.append((dup, [d.port for d in same]))
            self._send(same[0].index, p.body8(dup, p.ID_CONFLICT, 0))
        self.dispensers = [d for d in self.dispensers if d.id not in dups]
        for i, d in enumerate(self.dispensers):
            d.index = i

    # --------------------------------------------------------------- commands

    def ping(self, i: int) -> bool:
        return self._send(i, p.body32(self._id(i), p.PING, 0))

    def start(self, i: int) -> bool:
        return self._send(i, p.body8(self._id(i), p.SET_MOTOR_SPEED, 255, 1))

    def stop(self, i: int) -> bool:
        return self._send(i, p.body8(self._id(i), p.SET_MOTOR_SPEED, 0))

    def set_motor_direction(self, i: int, direction: int) -> bool:
        return self._send(i, p.body8(self._id(i), p.SET_MOTOR_DIRECTION, direction))

    def dispense_time(self, i: int, ms: int) -> bool:
        return self._send(i, p.body32(self._id(i), p.TIME_DISPENSE, ms))

    def dispense_ticks(self, i: int, ticks: int, speed: int = FULL_SPEED) -> bool:
        body = p.body16(self._id(i), p.TICK_SPEED_DISPENSE, ticks, speed)
        if self._send(i, body):
            return True
        log.error("dispense command failed, retrying once")
        return self._send(i, body)

    def is_dispensing(self, i: int) -> tuple[int, int]:
        """(dispensing, over_current); (-1, -1) if the reply got lost (motor noise),
        (1, 0) if the request itself failed - callers poll again."""
        self.ser.timeout = 0.1
        ok = self._send(i, p.body8(self._id(i), p.IS_DISPENSING, 0))
        self.ser.timeout = DEFAULT_TIMEOUT
        if ok:
            ack, body = self._receive()
            if ack == p.ACK_OK:
                return body[2], body[3]
            if ack == p.ACK_TIMEOUT:
                return -1, -1
        return 1, 0

    def update_liquid_levels(self) -> bool:
        return self._broadcast(p.body8(p.DEST_BROADCAST, p.UPDATE_LIQUID_LEVEL, 0))

    def get_liquid_level(self, i: int) -> int:
        if self._send(i, p.body8(self._id(i), p.LIQUID_LEVEL, 0)):
            ack, body = self._receive()
            if ack == p.ACK_OK:
                return _u16(body, 0)
        return -1

    def get_liquid_level_thresholds(self, i: int) -> tuple[int, int]:
        if self._send(i, p.body8(self._id(i), p.GET_LIQUID_THRESHOLDS, 0)):
            ack, body = self._receive()
            if ack == p.ACK_OK:
                return _u16(body, 0), _u16(body, 1)
        return -1, -1

    def set_liquid_level_thresholds(self, i: int, low: int, out: int) -> bool:
        return self._send(i, p.body16(self._id(i), p.SET_LIQUID_THRESHOLDS, low, out))

    def get_saved_tick_count(self, i: int) -> int:
        if self._send(i, p.body8(self._id(i), p.SAVED_TICK_COUNT, 0)):
            ack, body = self._receive()
            if ack == p.ACK_OK:
                return _u16(body, 0)
        return -1

    def flush_saved_tick_count(self) -> bool:
        return self._broadcast(p.body8(p.DEST_BROADCAST, p.FLUSH_SAVED_TICK_COUNT, 0))

    def get_version(self, i: int) -> int:
        if self._send(i, p.body8(self._id(i), p.GET_VERSION, 0)):
            self.ser.timeout = 0.1  # v2 firmware never answers
            ack, body = self._receive(quiet=True)
            self.ser.timeout = DEFAULT_TIMEOUT
            if ack == p.ACK_OK:
                return _u16(body, 0)
        return -1

    def comm_test(self) -> bool:
        self.router.sync(False)
        return self._send(0, p.body8(self._id(0), p.COMM_TEST, 0))

    def pattern_define(self, i: int, pattern: int) -> bool:
        return self._send(i, p.body8(self._id(i), p.PATTERN_DEFINE, pattern))

    def pattern_add_segment(self, i: int, red: int, green: int, blue: int, steps: int) -> bool:
        return self._send(i, p.body8(self._id(i), p.PATTERN_ADD_SEGMENT, red, green, blue, steps))

    def pattern_finish(self, i: int) -> bool:
        return self._send(i, p.body8(self._id(i), p.PATTERN_FINISH, 0))

    # LEDs: broadcast with the sync line low, then raise it so all start together.
    def _led(self, ptype: int, resync: bool = True) -> bool:
        self.router.sync(False)
        ok = self._broadcast(p.body8(p.DEST_BROADCAST, ptype, 0))
        if resync:
            time.sleep(0.01)
            self.router.sync(True)
        return ok

    def led_off(self) -> bool:
        return self._led(p.LED_OFF, resync=False)

    def led_idle(self) -> bool:
        return self._led(p.LED_IDLE)

    def led_dispense(self) -> bool:
        return self._led(p.LED_DISPENSE)

    def led_complete(self) -> bool:
        return self._led(p.LED_DRINK_DONE)

    def led_clean(self) -> bool:
        return self._led(p.LED_CLEAN)

    def set_status_color(self, red: int, green: int, blue: int) -> None:
        if self.status_led:
            self.status_led.set_color(bool(red), bool(green), bool(blue))

    # ------------------------------------------------------------- high level

    def pour_ml(self, amounts: dict[int, float], always_fast: bool = False, poll: float = 0.1,
                timeout: float = 120.0) -> None:
        """Pour {dispenser index: ml} at the same time and wait until all pumps stop.
        Same logic as mixer._dispense_recipe: amounts under 20 ml go at half speed."""
        for i, ml in amounts.items():
            if ml and not 0 < ml <= MAX_DISPENSE_ML:
                raise DriverError(f"refusing to pour {ml} ml (limit {MAX_DISPENSE_ML})")

        active: list[int] = []
        stalled = None
        try:
            for i, ml in amounts.items():
                if not ml:
                    continue
                ticks = int(ml * TICKS_PER_ML)
                speed = HALF_SPEED if ml < SLOW_DISPENSE_THRESHOLD and not always_fast else FULL_SPEED
                active.append(i)  # before sending: the command may arrive even if its ACK is lost
                self.set_motor_direction(i, p.MOTOR_DIRECTION_FORWARD)
                if not self.dispense_ticks(i, ticks, speed):
                    raise DriverError(f"dispense of {ticks} ticks at speed {speed} on dispenser #{i + 1} failed")
                time.sleep(0.01)

            deadline = time.monotonic() + timeout
            for i in active:
                while True:
                    if time.monotonic() > deadline:
                        raise DriverError("pour did not finish in time; pumps stopped")
                    dispensing, over_current = self.is_dispensing(i)
                    if dispensing < 0 or over_current < 0:
                        log.warning("is_dispensing on #%d failed (motor noise?), retrying", i + 1)
                        time.sleep(0.2)
                        continue
                    if over_current:
                        stalled = i
                        raise OverCurrentError(f"pump #{i + 1} stalled (over current); other pumps stopped")
                    if dispensing == 0:
                        break
                    time.sleep(poll)
        except BaseException:
            # Changed from mixer._dispense_recipe, which left the other pumps running when one
            # stalled or a dispense command failed. Stop every pump we started; the stalled one
            # has already stopped itself and ignores commands until reset.
            self._stop_quietly(j for j in active if j != stalled)
            raise

    # ---------------------------------------------------------------- private

    def _id(self, i: int) -> int:
        try:
            return self.dispensers[i].id
        except IndexError:
            raise DriverError(f"no dispenser #{i + 1} (found {self.count()})") from None

    def _stop_quietly(self, indexes) -> None:
        """Stop pumps during error handling without hiding the original error."""
        for j in indexes:
            try:
                if not self.stop(j):
                    log.error("could not stop pump #%d", j + 1)
            except Exception:
                log.exception("could not stop pump #%d", j + 1)

    def _broadcast(self, body: bytes) -> bool:
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        frame = p.encode(body)
        return self.ser.write(frame) == len(frame)

    def _send(self, i: int, body: bytes) -> bool:
        """Send to dispenser index i and wait for its ACK."""
        self.router.select(self.dispensers[i].port)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        frame = p.encode(body)
        what = f"dispenser #{i + 1}, packet type {body[1]}"
        if self.ser.write(frame) != len(frame):
            log.error("send timeout (%s)", what)
            return False
        ch = self.ser.read(1)
        if len(ch) < 1:
            log.error("no ACK (%s)", what)
            return False
        if ch[0] != p.ACK_OK:
            log.error("ACK %s (%s)", p.ACK_NAMES.get(ch[0], f"invalid code {ch[0]}"), what)
            return False
        return True

    def _receive(self, quiet: bool = False) -> tuple[int, bytes]:
        """Read one packet from the selected dispenser and ACK it."""
        header = 0
        while header < 2:
            ch = self.ser.read(1)
            if len(ch) < 1:
                if not quiet:
                    log.error("receive: response timeout")
                return p.ACK_TIMEOUT, b""
            header = header + 1 if ch[0] == 0xFF else 0

        ack, body = p.decode(self.ser.read(p.RAW_PACKET_SIZE))
        if ack != p.ACK_OK and not quiet:
            log.error("receive: %s", p.ACK_NAMES[ack])
        if self.ser.write(bytes([ack])) != 1:
            return p.ACK_TIMEOUT, b""
        return ack, (body if ack == p.ACK_OK else b"")


def _u16(body: bytes, n: int) -> int:
    return int.from_bytes(body[2 + 2 * n: 4 + 2 * n], "little")
