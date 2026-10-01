Introduction
============

Programs for the various systems of the Bartendro drink dispensing robot.

Created by Pierre Michael and Robert Kaye
Copyright (c) Party Robotics 2010-2013

All of the source code in this repository is licensed under the GNU Public License 2.0.
The hardware schematic and layouts are licensed using the Creative Commons 
Attribution-ShareAlike 3.0 Unported license.

The source tree is laid out as follows:

hardware -- schematics and layouts for the dispenser and router hardware boards
firmware -- source code (C) for the dispenser and router boards
scripts  -- scripts to make running the bartendro software easier
tsb      -- legacy code from our old skool drink bot prototyp Tequila Sunrise Bot
ui       -- web interface for the bot, written in python.

These various subdirectories may contain more README and COPYING files.

Python 3 rewrite (this fork, branch `modernize`)
===============================================

The Pi software in `ui/` is Python 2 (Flask 0.12, memcached) and no longer installs on
current Raspberry Pi OS. `src/bartendro` is a Python 3 rewrite for Pi 3B+ / Pi 4 on
Raspberry Pi OS Bookworm. It keeps the existing router board, dispensers and firmware.

src/bartendro/hw -- serial protocol, router board (I2C), status LED, dispenser driver,
                    and a simulator that speaks the real protocol (replaces "software only")
src/bartendro/cli.py -- `bartendro-hw`: discover, flash LEDs, pour, run, level, info
src/bartendro/db -- SQLite database (SQLAlchemy + Alembic migrations), importer for old
                    bartendro.db files; `bartendro-db`: upgrade, import, show, set-password
src/bartendro/config.py -- per-bot settings file (docs/bartendro.example.toml)
src/bartendro/bot.py -- the drink-making logic (port of the old mixer and state machine)
src/bartendro/data/classics.toml -- bundled drinks (poured over ice, no shaking):
                     `bartendro-db load-recipes`, `bartendro-db suggest 3` (plan a small bot)
src/bartendro/web -- web app (FastAPI): menu, shots, admin, live pour status;
                     `bartendro-web --sim 15` runs it on a PC with simulated pumps
tests            -- pytest suite, runs against the simulator (no hardware needed)
docs/pi-setup.md -- Pi setup (UART, I2C, install) and the first hardware test

Done so far: milestone 1 (hardware layer + command-line test tool), milestone 2 (database,
importer for old `bartendro.db` files, per-bot config), a first web app (milestone 3) and a
bundled recipe collection with a bottle planner (milestone 4). Next: install scripts (systemd,
hotspot, kiosk). `ui/` stays until the new code pours correctly on real bots.

    pip install -e ".[dev]" && pytest
    bartendro-hw --sim 15 discover
    bartendro-db --db demo.db import ui/bartendro.db.default
    bartendro-db --db demo.db load-recipes
    bartendro-web --sim 15 --db demo.db      # then open http://localhost:8080

Downloading updated SD Card images
==================================

In 2021 the SD card images were updated. They can be downloaded here:

https://github.com/partyrobotics/bartendro/releases/tag/v-2021-05-21
