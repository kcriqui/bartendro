"""Open the real hardware (serial port + router + status LED) or the simulator."""

from __future__ import annotations

import os
from contextlib import contextmanager

from . import protocol as p
from .driver import Driver
from .router import RouterSelect
from .simulator import SimBus
from .status_led import StatusLED

# /dev/serial0 points at the GPIO 14/15 UART on every Pi model. With
# dtoverlay=disable-bt it is the full PL011 UART (ttyAMA0) - recommended.
SERIAL_CANDIDATES = ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0"]


class ConnectError(Exception):
    pass


def find_serial_device() -> str:
    for dev in SERIAL_CANDIDATES:
        if os.path.exists(dev):
            return dev
    raise ConnectError(
        "no Pi UART found (tried " + ", ".join(SERIAL_CANDIDATES) + "). "
        "Enable the serial port: see docs/pi-setup.md"
    )


@contextmanager
def open_driver(device: str | None = None, i2c_bus: int = 1, simulate: int = 0,
                status_led: bool = True):
    """Yield a Driver that has already discovered the dispensers."""
    if simulate:
        bus = SimBus.with_dispensers(simulate)
        driver = Driver(bus.serial, bus.router)
        driver.sim = bus
        driver.discover()
        yield driver
        return

    try:
        import serial
    except ImportError as e:
        raise ConnectError("pyserial is not installed") from e

    device = device or find_serial_device()
    try:
        ser = serial.Serial(device, p.BAUD_RATE, bytesize=serial.EIGHTBITS,
                            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.01)
    except serial.SerialException as e:
        raise ConnectError(f"cannot open {device}: {e}") from e

    router = RouterSelect(bus=i2c_bus)
    led = StatusLED() if status_led else None
    try:
        router.open()
        if led and not led.open():
            led = None
        driver = Driver(ser, router, led)
        driver.device = device
        driver.discover()
        yield driver
    finally:
        router.close()
        if led:
            led.close()
        ser.close()
