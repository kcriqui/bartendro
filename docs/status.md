# Status, plan and backlog

Where the project stands. Update the "Current state" section whenever something lands.

## Goal

Replace the Python 2 Pi software (`ui/`) with a maintainable Python 3 app, keeping the original
router board, peristaltic dispensers and AVR firmware (`firmware/`, unchanged). Kevin's bots:
2 full Bartendros (15 dispensers each), plus 2-3 small 3-pump bots planned ("margaritabot",
"manhattanbot") using the same router board (the 3-port mini-router). **Pi 3B+ or Pi 4 only -
Kevin won't go beyond a Pi 4** (2026-10-23). Raspberry Pi OS Bookworm, Python 3.11: keep the code
3.11-compatible. One codebase, per-bot config.

## Milestones

1. **Hardware layer + `bartendro-hw` CLI** - code done 2026-10-01, tested only in the simulator
   and against the firmware's pack7.c (500 packets identical). **Next step: run it on a real bot**
   ([pi-setup.md](pi-setup.md), "What to check on real hardware") and fix what turns up.
2. **Database** - done 2026-10-01: SQLAlchemy 2 + Alembic on SQLite, importer for old
   `bartendro.db` files (`--no-logs --no-settings` = recipes + bottles only), per-bot TOML config,
   `bartendro-db` CLI. The bot database is `build/bartendro.db` (see
   [project-notes.md](project-notes.md#bot-database-2026-10-02)).
3. **Web app** - done 2026-10-01/02 (simulator only): FastAPI + Jinja + plain JS, WebSocket status,
   original Bartendro look, menus by category, parties, admin editor. Details:
   [architecture.md](architecture.md#web-app). Not done yet: old-db upload in the UI,
   liquid-level calibration page, "feeling lucky" / shotbot UI / turbo options.
4. **Recipe database** - done 2026-10-01: `src/bartendro/data/classics.toml`, 62 drinks. Rules:
   [recipes.md](recipes.md).
5. **Install script / systemd service, NetworkManager hotspot, Chromium kiosk** - not started.
   Includes the SD-card protection plan and the kiosk for the Waveshare 10.1" screen
   ([project-notes.md](project-notes.md#pi-model-and-performance-2026-10-02)).

## Current state (2026-10-31)

- `modernize` pushed (2026-10-31): 124 tests
  pass, ruff clean, migrations 0001-0007. Everything runs only against the simulator so far.
- **Review:** [PR #1](https://github.com/kcriqui/bartendro/pull/1) (`modernize` -> `master` in the
  fork) opened 2026-10-03 for Kevin's friend Chris (christopherheiny@gmail.com) to review. No
  comments yet as of 2026-10-24. New pushes to `modernize` show up in it automatically. Don't merge
  it unasked.
- **Simulator page** `/sim` (2026-10-31): the 15 simulated pumps (3 x 5) with LEDs, rotors, ml and
  levels, plus Jam / level buttons for testing errors. Building it found a real bug: Reset didn't
  restart a stalled dispenser (fixed; see [pi-setup.md](pi-setup.md#what-to-check-on-real-hardware)).
  Next idea: per-pump LEDs (only the pumps in the drink show "pouring", each turns green when
  done; party colours later) - the firmware already allows it, see
  [project-notes.md](project-notes.md#dispenser-leds-2026-10-31).
- **Pour mixing** (2026-10-31, Kevin: "the top is super strong"): drinks now pour the shorter
  ingredients in bursts spread over the longest one (Admin > Options > pour mixing: pulsed /
  together / off). Timing needs the real pump speed - see
  [pi-setup.md](pi-setup.md#what-to-check-on-real-hardware). Covers David's "pulse the pumps" item.
- **Hosted demo** live at https://truenas-scale.tail4c32e5.ts.net, watched twice a day
  ([hosting.md](hosting.md)).
- **Bot database** (build/bartendro.db, copy in deploy/demo-bot.db): 200 drinks, "Tequila, Anejo"
  names, parties Halloween / Yurtville Weekend / Bar2D2, all inactive.

## Next steps

1. First real-hardware test: Pi + mini-router + 1-2 pumps ([pi-setup.md](pi-setup.md)).
2. Milestone 5: install script, systemd, hotspot, kiosk.
3. Small-pour tuning with real pumps and a scale (ideas in [recipes.md](recipes.md#small-pours)).

## Backlog / open items

- **UI feedback from David**: [../Davids-ideas.md](../Davids-ideas.md) - a checklist of UI changes
  (capitalised headings, portrait layout, pulsed pumps for mixing, "Our Favorites" / "Cocktail
  Categories", 6+6 menu, taster / sweeter-tarter / strength fixes, "place cup under spout" pop-up,
  admin renames and sort, ingredient types Alcohol / Mixer / Sweet / Sour / Garnish...). Not
  started; discuss with Kevin before doing any of it.
- LED sign font: 9x15 bold for now; Kevin may supply reference fonts. Pixel Operator (CC0) wasn't
  reachable to try.
- Yurtville Weekend party has no drink list yet (= every drink).
- Demo password: only if scanners pile up (Kevin's call, 2026-10-03). Not needed so far.
- Possible: publish a prebuilt demo image (GHCR) if building on the NAS keeps failing.
