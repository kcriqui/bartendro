"""A software model of the router + dispensers, speaking the real wire protocol.

Replaces the old "software only" mode (which skipped the driver entirely): here the real
Driver code runs against simulated hardware, so discovery, packets, CRCs and pours are
all exercised without a bot.

Model: every dispenser hears what the Pi sends (that's how broadcasts work); only the
dispenser on the router's selected port can answer.
"""

from __future__ import annotations

import time

from . import protocol as p

TICKS_PER_SECOND = 40.0  # rough pump speed at full speed; only matters for timing


class SimDispenser:
    def __init__(self, dispenser_id: int, level: int = 200, version: int = 3):
        self.id = dispenser_id
        self.level = level
        self.version = version
        self.low_threshold = 120
        self.out_threshold = 75
        self.saved_ticks = 0
        self.direction = p.MOTOR_DIRECTION_FORWARD
        self.motor_until = 0.0
        self.over_current = False  # the firmware's current-sense flag: a stalled pump
        self.jammed = False  # the sim page's "jam": the motor stalls as soon as it runs
        self.led = "off"  # LED pattern: off / idle / dispense / done / clean / current_sense
        self.id_conflict = False
        self.packet_mode = False
        self.poured_ticks = 0
        self._rx = bytearray()
        # the motor run in progress (or the last one), for the sim page's live tick count
        self._run_start = 0.0
        self._run_rate = 0.0  # ticks per second of real time
        self._run_target = 0.0  # ticks (inf for timed runs)
        self._ticks_before = 0.0  # ticks pumped by earlier runs

    @property
    def dispensing(self) -> bool:
        return time.monotonic() < self.motor_until and not self.over_current

    def hear(self, data: bytes) -> bytes:
        """Feed bytes from the Pi; return what this dispenser would send back."""
        out = bytearray()
        for b in data:
            if not self.packet_mode:
                if b == ord("?"):
                    out.append(self.id)
                elif b == 0xFF:
                    self.packet_mode = True
                    self._rx = bytearray(b"\xff")
                continue
            self._rx.append(b)
            if self._rx[-2:] == b"\xff\xff":
                self._rx = bytearray(b"\xff\xff")
                continue
            if len(self._rx) >= 2 + p.RAW_PACKET_SIZE and self._rx[:2] == b"\xff\xff":
                out += self._handle(bytes(self._rx[2:2 + p.RAW_PACKET_SIZE]))
                self._rx = bytearray()
            elif len(self._rx) > 2 + p.RAW_PACKET_SIZE:
                self._rx = bytearray()
        return bytes(out)

    def _handle(self, raw: bytes) -> bytes:
        ack, body = p.decode(raw)
        dest, ptype = (body[0], body[1]) if body else (None, None)
        if dest not in (self.id, p.DEST_BROADCAST):
            return b""
        broadcast = dest == p.DEST_BROADCAST
        if ack != p.ACK_OK:
            return b"" if broadcast else bytes([p.ACK_CRC_FAIL])
        reply = None
        u16 = lambda n: int.from_bytes(body[2 + 2 * n:4 + 2 * n], "little")
        cs = self.over_current  # like the firmware: a stalled pump ignores motor / LED commands
        if ptype == p.TICK_SPEED_DISPENSE:
            if not cs:
                self._run(u16(0), u16(1))
        elif ptype == p.TICK_DISPENSE:
            if not cs:
                self._run(int.from_bytes(body[2:6], "little"), 255)
        elif ptype == p.TIME_DISPENSE:
            if not cs:
                self._start(int.from_bytes(body[2:6], "little") / 1000, TICKS_PER_SECOND, float("inf"))
        elif ptype == p.SET_MOTOR_SPEED:
            if not body[2]:
                self.motor_until = min(self.motor_until, time.monotonic())
            elif not cs:
                self._start(3600, TICKS_PER_SECOND * body[2] / 255, float("inf"))
        elif ptype == p.SET_MOTOR_DIRECTION:
            self.direction = body[2]
        elif ptype == p.IS_DISPENSING:
            reply = p.body8(0, ptype, int(self.dispensing), int(self.over_current))
        elif ptype == p.LIQUID_LEVEL:
            reply = p.body16(0, ptype, self.level, 0)
        elif ptype == p.GET_LIQUID_THRESHOLDS:
            reply = p.body16(0, ptype, self.low_threshold, self.out_threshold)
        elif ptype == p.SET_LIQUID_THRESHOLDS:
            self.low_threshold, self.out_threshold = u16(0), u16(1)
        elif ptype == p.SAVED_TICK_COUNT:
            reply = p.body16(0, ptype, self.saved_ticks & 0xFFFF, 0)
        elif ptype == p.GET_VERSION:
            if self.version < 3:
                return b"" if broadcast else bytes([p.ACK_OK])  # v2: ACK but no answer
            reply = p.body16(0, ptype, self.version, 0)
        elif ptype == p.ID_CONFLICT:
            self.id_conflict = True
        elif ptype == p.LED_OFF or (ptype in (p.LED_IDLE, p.LED_DISPENSE, p.LED_DRINK_DONE, p.LED_CLEAN)
                                    and not cs):
            self.led = {p.LED_OFF: "off", p.LED_IDLE: "idle", p.LED_DISPENSE: "dispense",
                        p.LED_DRINK_DONE: "done", p.LED_CLEAN: "clean"}[ptype]
        if broadcast:
            return b""
        out = bytes([p.ACK_OK])
        if reply is not None:
            out += p.encode(reply)
        return out

    def _run(self, ticks: int, speed: int) -> None:
        rate = TICKS_PER_SECOND * max(speed, 1) / 255
        self._start(ticks / rate / SimBus.time_scale, rate * SimBus.time_scale, ticks)
        self.poured_ticks += ticks
        self.saved_ticks += ticks

    def _start(self, seconds: float, rate: float, target: float) -> None:
        now = time.monotonic()
        self._ticks_before += self._run_ticks(now)
        self._run_start, self._run_rate, self._run_target = now, rate, target
        self.motor_until = now + seconds
        if self.jammed:
            self.stall()

    def _run_ticks(self, now: float) -> float:
        elapsed = max(0.0, min(now, self.motor_until) - self._run_start)
        return min(self._run_target, elapsed * self._run_rate)

    def stall(self) -> None:
        """What the firmware does when the current sense trips: stop, blink red, ignore commands
        until the router resets it."""
        self.motor_until = min(self.motor_until, time.monotonic())
        self.over_current = True
        self.led = "current_sense"

    def boot(self) -> None:
        """The router's reset line restarts the dispenser (clears a stall; a jam stays)."""
        self.packet_mode = False
        self.over_current = False
        self.motor_until = min(self.motor_until, time.monotonic())
        self.led = "off"

    def snapshot(self) -> dict:
        """State for the sim page."""
        now = time.monotonic()
        running = self.dispensing
        return {"id": self.id, "led": self.led, "running": running,
                "reverse": self.direction != p.MOTOR_DIRECTION_FORWARD,
                "ticks": round(self._ticks_before + self._run_ticks(now), 1),
                "run_ticks": round(self._run_ticks(now), 1),
                "run_target": None if self._run_target == float("inf") else self._run_target,
                "level": self.level, "low": self.low_threshold, "out": self.out_threshold,
                "stalled": self.over_current, "jammed": self.jammed, "id_conflict": self.id_conflict}


class SimBus:
    """Fake serial port + router. Use `.serial` and `.router` with the Driver."""

    time_scale = 1.0  # tests set this high so pours finish instantly

    def __init__(self, dispensers: dict[int, SimDispenser]):
        self.ports = dispensers  # router port -> dispenser
        self.selected: int | None = None
        self.sync_on = False
        self.timeout = 2.0
        self._in = bytearray()
        self.serial = _SimSerial(self)
        self.router = _SimRouter(self)

    @classmethod
    def with_dispensers(cls, n: int, **kw) -> SimBus:
        return cls({port: SimDispenser(dispenser_id=port + 1, **kw) for port in range(n)})


class _SimSerial:
    def __init__(self, bus: SimBus):
        self.bus = bus

    @property
    def timeout(self):
        return self.bus.timeout

    @timeout.setter
    def timeout(self, v):
        self.bus.timeout = v

    def write(self, data: bytes) -> int:
        for port, d in self.bus.ports.items():
            reply = d.hear(data)
            if port == self.bus.selected:
                self.bus._in += reply
        return len(data)

    def read(self, n: int) -> bytes:
        out = bytes(self.bus._in[:n])
        del self.bus._in[:n]
        return out

    def reset_input_buffer(self) -> None:
        self.bus._in.clear()

    def reset_output_buffer(self) -> None:
        pass

    def close(self) -> None:
        pass


class _SimRouter:
    def __init__(self, bus: SimBus):
        self.bus = bus

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def reset(self) -> None:
        self.bus.selected = None
        for d in self.bus.ports.values():
            d.boot()

    def select(self, port: int) -> None:
        self.bus.selected = port

    def sync(self, on: bool) -> None:
        self.bus.sync_on = on
