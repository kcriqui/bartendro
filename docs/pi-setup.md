# Raspberry Pi setup (Pi 3B+ / Pi 4, Raspberry Pi OS Bookworm)

For the Python 3 rewrite (`src/bartendro`). Pi 3B+ and Pi 4 are the supported boards.
Raspberry Pi OS Lite (64-bit) is enough; add a desktop only for a touchscreen kiosk.

## 1. Serial port to the router board

The router is wired to the Pi's GPIO UART (pins 8/10). On the 3B+ and 4 that UART is
taken by Bluetooth by default, leaving only the "mini UART", whose speed depends on the
CPU clock. Give the good UART (PL011) back to the GPIO pins:

```
sudo raspi-config nonint do_serial_hw 0     # serial hardware on
sudo raspi-config nonint do_serial_cons 1   # no login console on it
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/firmware/config.txt
sudo systemctl disable hciuart
```

After a reboot `/dev/serial0` points to `/dev/ttyAMA0`; `bartendro-hw` uses
`/dev/serial0` by default (override with `--device`).

## 2. I2C to the router board

```
sudo raspi-config nonint do_i2c 0
```

The router answers at address 4 on bus 1 (`i2cdetect -y 1` from `i2c-tools` shows `04`).

## 3. Install

```
sudo apt install -y python3-venv python3-gpiozero python3-lgpio git i2c-tools
sudo usermod -aG dialout,i2c,gpio $USER     # then log out and in again
git clone https://github.com/kcriqui/bartendro.git
cd bartendro && git checkout modernize
python3 -m venv --system-site-packages .venv   # system packages = gpiozero/lgpio from apt
.venv/bin/pip install -e .
```

## 4. First test (milestone 1)

```
.venv/bin/bartendro-hw -v discover      # finds dispensers, pings each one
.venv/bin/bartendro-hw flash            # all dispenser LEDs cycle together
.venv/bin/bartendro-hw status-led green # case LED
.venv/bin/bartendro-hw level            # liquid level sensors
.venv/bin/bartendro-hw pour 1 30        # 30 ml from dispenser #1 (asks first)
.venv/bin/bartendro-hw run 1 2000       # run pump #1 for 2 s (prime a line)
.venv/bin/bartendro-hw run 1 2000 --reverse
```

`bartendro-hw --sim 15 discover` runs the same commands against simulated dispensers
(useful on a PC). The old Python 2 server (`ui/`) must not run at the same time - it holds
the serial port.

## 5. Bot config and database

```
sudo mkdir -p /etc/bartendro /var/lib/bartendro && sudo chown $USER /var/lib/bartendro
sudo cp docs/bartendro.example.toml /etc/bartendro/bartendro.toml   # then edit: name, ports, ...
.venv/bin/bartendro-db upgrade          # creates /var/lib/bartendro/bartendro.db
```

To keep the drinks and dispenser setup from an old bot, copy its `bartendro.db` off the old SD
card. It's in the `ui/` folder of the old checkout (e.g. `/home/<user>/bartendro/ui/bartendro.db`;
the old start script used `/home/robert/bartendro/ui`). Then:

```
.venv/bin/bartendro-db import /path/to/old/bartendro.db   # the old file is only read
.venv/bin/bartendro-db show                               # dispensers + drinks it can make
```

The import lists anything it skipped or changed. Old custom drinks ("customizable margarita")
and the wanted-drinks list are not carried over. The admin password is kept but stored hashed;
`bartendro-db set-password` changes it.

## 6. Web app

```
.venv/bin/bartendro-web            # http://<pi address>:8080 from a phone, or the Pi's own screen
```

It uses the config file for the hardware and database. The old Python 2 server must not run
at the same time. Starting it at boot (systemd), the hotspot and the kiosk browser come with
milestone 5.

## Mini-router (3-port board)

The 3-port mini-router (`hardware/minirouter`, `firmware/mini-router`) talks to the Pi exactly
like the 15-port router (same I2C address and commands), so everything above applies. Pass
`--ports 3` so discovery only scans the ports it has:

```
.venv/bin/bartendro-hw --ports 3 -v discover
```

- Port numbering: port 0 = PD4, 1 = PD3, 2 = PD0 on the AVR. Which connector that is isn't
  marked in the firmware: plug one dispenser in, run `discover`, note the port it reports.
- Its firmware still uses the pre-August-2013 way of relaying dispenser replies (only on a change
  against a remembered pin level), and it doesn't set the reply line at power-up. Expect a few
  "inconsistent, retrying" lines on the first port after a reset; discovery retries them. If
  replies stay garbled, lost ACKs or "is_dispensing ... failed" warnings show up on a mini-router
  but not on the 15-port router, suspect this firmware: the 15-port router's relaying code can be
  ported to it (needs avr-gcc and an AVR programmer).

## What to check on real hardware

- All dispensers found, in port order; no "ID CONFLICT" lines.
- `flash`: every dispenser LED changes pattern at the same moment (router sync line).
- Status LED colours: the old code swaps green/blue when the dispensers report firmware
  v3+. If `status-led green` shows blue, note the firmware version from `discover`.
- `pour 1 60` into a measuring cup: the old calibration is 2.78 ticks/ml.
- `level` gives values above 120 for full bottles (low <= 120, out <= 75).
- **Reset after a stall** (2026-10-31): Reset now restarts the dispensers through the router and
  finds them again, like the old app. Before that fix our Reset only sent `led_idle`, which a
  stalled dispenser ignores (the firmware ignores motor and LED commands until it restarts), so
  the pump would have stayed dead. Check: block a tube (or hold the rotor) during a test pour,
  see the dispenser blink red and the screen ask for a reset, press Reset, pour again.
- A stall shows up within one polling round (~0.1 s plus a few ms per running pump): since
  pulsed pours (2026-10-31) the pour loop checks every running pump each round. The old code
  waited for one pump at a time, so a stall on a later pump showed only after the earlier ones.
- **Pump speed for pulsed pours** (option "pump ticks per second", default 40 = a guess): run one
  pump at full speed for a timed 10 s (`bartendro-hw ... run`), weigh it, and convert:
  ticks per second = ml / 10 x the pump's ticks per ml. If it's far off, pulsed bursts bunch up at
  the start (rate set too high) or drift late. Then pour a layered-looking drink (juice + spirit)
  into a clear glass with pour mixing = off, together and pulsed, and taste the top. Also check
  that half speed (166) really is about 166/255 of full speed - the schedule assumes it is.
- Pulsed pours stop and start the motors several times per drink: listen for stutter, check the
  totals still match a scale (each burst is whole ticks; the last burst takes the remainder).
- **Sim page on the Pi's own screen** (needs `bartendro-web --sim 15` on the Pi, no pumps): open
  `/sim` in a second Chromium window on the Pi next to the kiosk menu (ideally a party with the LED
  sign on) and check the menu still scrolls and responds smoothly, and `top` shows how much CPU
  Chromium and `bartendro-web` take with and without the sim window. The sim page redraws 15
  glowing LED rings up to 60 times a second, even when idle; the glow (CSS drop-shadow) is the
  likely cost. Watching it from a phone or PC costs the Pi almost nothing (the server sends ~10
  small updates a second). If it's sluggish: add a light mode (no glow, ~20 fps, no redraw when
  nothing changed) to `static/sim.js`.
