# Bartendro (Kevin's fork) - Python 3 rewrite

Fork of partyrobotics/bartendro: `origin` = github.com/kcriqui/bartendro, `upstream` = partyrobotics.
Work happens on branch `modernize`. Lives on the NAS (`P:\Claude\bartendro` =
`\\TRUENAS\Projects\Claude\bartendro`). This project is separate from Kevin's eBay tools
(`P:\Claude\starlight-ebay-template`) - never touch that from here.

## Goal
Replace the Python 2 Pi software (`ui/`) with a maintainable Python 3 app, keeping the
original router board, peristaltic dispensers and AVR firmware (`firmware/`, unchanged).
Kevin's bots: 2 full Bartendros (15 dispensers each), plus 2-3 small 3-pump bots planned
("margaritabot", "manhattanbot") using the same router board. Pi 3B+ or Pi 4, Raspberry Pi OS
Bookworm (Python 3.11 - keep code 3.11-compatible). One codebase, per-bot config.

## Milestones
1. **Hardware layer + `bartendro-hw` CLI** - code done 2026-10-01, tested only in the simulator
   and against the firmware's pack7.c (500 packets identical). **Next step: run it on a real
   bot** (docs/pi-setup.md, "What to check on real hardware") and fix what turns up.
2. **Database** - done 2026-10-01: SQLAlchemy 2 + Alembic on SQLite, importer for old
   `bartendro.db` files (checked against `ui/bartendro.db.default`), per-bot TOML config,
   `bartendro-db` CLI. Still to do: import Kevin's real `bartendro.db` files from the bots' SD cards.
3. **Web app** - first version done 2026-10-01, tested with the simulator only: FastAPI +
   Jinja templates + ~200 lines of plain JS (no htmx: the hotspot has no internet to fetch it),
   WebSocket pour status to every screen. No login (Kevin's call): anyone on the bot's WiFi
   can use admin. Kiosk on localhost + phones at the same time. Not done yet: old-db upload in
   the UI, liquid-level calibration page, "feeling lucky" / shotbot UI / turbo options.
4. **Recipe database** - done 2026-10-01: `src/bartendro/data/classics.toml`, 54 drinks
   written for this project (amounts are facts from classic/IBA specs; no copied text - the
   GitHub IBA datasets are scrapes of IBA's site + Wikipedia, so not bundled; TheCocktailDB must
   not be redistributed). **Kevin's rules:**
   - The bot only pours liquids. The guest does the rest by hand, before the pour (absinthe
     rinse, muddled mint/lime) or after (dashes of bitters, shake/stir per `finish`).
   - By-hand recipe lines: counted units (parts NULL: dash, leaf, wedge, tsp...) or measured
     amounts of `manual` ingredients (half and half / cream are never pumped - too hard to
     clean). `step` = before/after; the UI shows a checklist before pouring.
   - What's available by hand is tracked like the pumps: `Ingredient.on_hand` (Admin >
     Dispensers > On hand). Drinks only show when pumped bottles are loaded AND by-hand items
     are on hand.
   - Specific spirits stay specific: a recipe asking for Reposado Tequila needs reposado
     (plain Tequila won't do); a reposado bottle can make generic-Tequila drinks.
   Loader matches existing ingredients by name/alias, links brands (Kahlua -> Coffee Liqueur)
   and `generic` children; classic drinks pour their recipe total (size_ml). "One step away"
   (load a bottle / get something on hand) + `suggest N` bottle planner.
5. Install script / systemd service, NetworkManager hotspot, optional Chromium kiosk.

## Layout
- `src/bartendro/hw/protocol.py` - packet format, CRC16, 7-bit packing (must match
  `firmware/common/packet.h` and `pack7.c`).
- `hw/router.py` (smbus2, bus 1, address 4), `hw/status_led.py` (gpiozero, BCM 24/23/25 =
  board pins 18/16/22), `hw/driver.py` (port of `ui/bartendro/router/driver.py` + the pour loop
  of `mixer._dispense_recipe`), `hw/simulator.py` (fake router + dispensers speaking the real
  protocol), `hw/connect.py` (open hardware or simulator).
- `db/models.py` (tables), `db/migrations/` (Alembic, no alembic.ini; run via `db.upgrade()`),
  `db/importer.py` (old bartendro.db -> new, read-only, keeps ids), `db/options.py` (typed
  settings, password hashed), `db/menu.py` (what can be made; parts -> ml with strength/tartness).
  Recipes are in parts, scaled to the glass size at pour time, like the old app.
- `bot.py` - port of mixer.py + fsm.py: one action at a time (BusyError), drink -> dispenser
  plan, shots, test dispense, pump runs, clean, levels, CURRENT_SENSE / ERROR states + reset.
  `background=True` runs the pumping in a thread; listeners get status/pour events.
- `web/` - `create_app(bot)` (routes, JSON API under /api, WebSocket /ws), `server.py`
  (`bartendro-web [--sim N]`, port 8080), `templates/`, `static/` (style.css, app.js).
- `db/recipes.py` - loads classics.toml (or a file like it): `bartendro-db load-recipes`,
  Admin > Drinks button. `db/menu.py` also has one_bottle_away() and suggest_bottles().
- `config.py` - per-bot TOML (`docs/bartendro.example.toml`): ports, dispensers, db path, screen.
- Schema change: edit models.py, then `bartendro-db --db scratch.db revision -m "..."`, review
  the generated file (add server_default for new NOT NULL columns); `test_migrations_match_models`
  fails until you do, `test_upgrade_keeps_data` checks a populated db survives. Migrations run
  with foreign keys off in one transaction (db/migrations/env.py) - batch mode drops and
  recreates tables, which otherwise breaks or cascade-deletes referencing rows.
- Port old logic faithfully; when changing behaviour, note it in a comment (e.g. discovery
  retries are now bounded).

## Testing
- `pip install -e ".[dev]"` then `pytest` (no hardware needed). `bartendro-hw --sim N ...`.
- Real hardware: never run pumps without Kevin's go-ahead (the CLI asks before pumping unless `-y`).
