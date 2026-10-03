"""I2C link to the router board, which connects the Pi's serial port to one dispenser
at a time (or all of them for broadcasts). Ported from ui/bartendro/router/dispenser_select.py.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

ROUTER_BUS = 1
ROUTER_ADDRESS = 4
CMD_SYNC_ON = 251
CMD_SYNC_OFF = 252
CMD_PING = 253
CMD_COUNT = 254
CMD_RESET = 255


class RouterError(Exception):
    pass


class RouterSelect:
    """Selects the dispenser port (0-14) the serial line is routed to."""

    def __init__(self, bus: int = ROUTER_BUS, address: int = ROUTER_ADDRESS, max_ports: int = 15):
        self.bus_number = bus
        self.address = address
        self.max_ports = max_ports
        self._bus = None

    def open(self) -> None:
        try:
            from smbus2 import SMBus
        except ImportError as e:
            raise RouterError("smbus2 is not installed (pip install smbus2)") from e
        try:
            self._bus = SMBus(self.bus_number)
        except OSError as e:
            raise RouterError(
                f"cannot open I2C bus {self.bus_number}: {e}. Is I2C enabled (raspi-config)?"
            ) from e

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None

    def _write(self, byte: int) -> None:
        try:
            self._bus.write_byte(self.address, byte)
        except OSError as e:
            log.warning("router write failed (%s), retrying once", e)
            try:
                self._bus.write_byte(self.address, byte)
            except OSError as e2:
                raise RouterError(f"cannot write to router at I2C address {self.address}: {e2}") from e2

    def reset(self) -> None:
        self._write(CMD_RESET)
        time.sleep(0.15)

    def select(self, port: int) -> None:
        if 0 <= port < self.max_ports:
            self._write(port)
            time.sleep(0.01)

    def sync(self, on: bool) -> None:
        """LED sync line, used to start LED animations on all dispensers at once."""
        self._write(CMD_SYNC_ON if on else CMD_SYNC_OFF)
