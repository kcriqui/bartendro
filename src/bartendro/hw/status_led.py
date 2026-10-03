"""The RGB status LED on the Bartendro case, wired to Pi GPIO.

The old code used RPi.GPIO in BOARD numbering: red = pin 18, green = pin 16, blue = pin 22
(BCM 24, 23, 25). With v3+ dispensers the old code swapped green and blue.
Uses gpiozero (preinstalled on Raspberry Pi OS; lgpio backend), which works on Pi 3B+/4/5.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

RED_BCM = 24  # board pin 18
GREEN_BCM = 23  # board pin 16
BLUE_BCM = 25  # board pin 22


class StatusLED:
    def __init__(self) -> None:
        self._leds = None
        self.swapped = False

    def open(self) -> bool:
        try:
            from gpiozero import LED
        except ImportError:
            log.warning("gpiozero not available: status LED disabled")
            return False
        try:
            self._leds = {
                "red": LED(RED_BCM),
                "green": LED(GREEN_BCM),
                "blue": LED(BLUE_BCM),
            }
        except Exception as e:  # noqa: BLE001 - gpiozero raises several types when no GPIO is present
            log.warning("cannot open status LED GPIO (%s): status LED disabled", e)
            self._leds = None
            return False
        return True

    def swap_blue_green(self) -> None:
        self.swapped = True

    def set_color(self, red: bool, green: bool, blue: bool) -> None:
        if not self._leds:
            return
        if self.swapped:
            green, blue = blue, green
        for name, on in (("red", red), ("green", green), ("blue", blue)):
            led = self._leds[name]
            led.on() if on else led.off()

    def close(self) -> None:
        if self._leds:
            for led in self._leds.values():
                led.close()
        self._leds = None
