# Bartendro (Kevin's fork) - Python 3 rewrite

Fork of partyrobotics/bartendro: `origin` = github.com/kcriqui/bartendro, `upstream` = partyrobotics.
Work happens on branch `modernize`. Lives on the NAS (`P:\Claude\bartendro` =
`\\TRUENAS\Projects\Claude\bartendro`). This project is separate from Kevin's eBay tools
(`P:\Claude\starlight-ebay-template`) - never touch that from here.

## Working from two PCs (home and office)
Kevin works on this from his home PC and his office PC, so **everything this project creates
lives here on the NAS**, not in a PC's local folders:
- Plans: `docs/plans/<date>-<topic>.md`. Plan mode writes to `~/.claude/plans/` on the local PC -
  copy the approved plan into `docs/plans/` and delete the local copy.
- Notes, decisions, findings: `docs/project-notes.md` (and this file), not Claude's local memory.
- Scratch / demo databases, renders, logs: `build/` (git-ignored, but on the NAS). Preview servers
  in `.claude/launch.json` use relative paths into `build/`.
- Per-PC setup (once on each PC): Python 3.11+, `pip install -e ".[dev]"` in the repo (puts
  `bartendro-hw` / `-db` / `-web` on PATH), `gh auth login`. If git complains about "dubious
  ownership" on the share: `git config --global --add safe.directory '%(prefix)///TRUENAS/Projects/Claude/bartendro'`.

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
   `bartendro.db` files (`--no-logs --no-settings` for recipes + bottles only), per-bot TOML
   config, `bartendro-db` CLI. **The bot database is `build/bartendro.db`** (2026-10-02): Lunarville's
   recipes + bottles + the bundled drinks + 6 from the old default db, 216 drinks, drink_size 100.
   How it was merged and what Kevin chose: docs/project-notes.md "Bot database". Old dbs rename
   drinks ("Margarita, SND", "zzz ..." = retired), so match on description/recipe, not just name.
3. **Web app** - first version done 2026-10-01, tested with the simulator only: FastAPI +
   Jinja templates + ~200 lines of plain JS (no htmx: the hotspot has no internet to fetch it),
   WebSocket pour status to every screen. No login (Kevin's call): anyone on the bot's WiFi
   can use admin. Kiosk on localhost + phones at the same time. Not done yet: old-db upload in
   the UI, liquid-level calibration page, "feeling lucky" / shotbot UI / turbo options.
   2026-10-02: original Bartendro look as the default theme (colours from
   ui/content/static/css/bartendro.css, logo/robot images; `scripts/render_old_ui.py` renders
   the old templates to build/old-ui for comparison); menu = "the essentials" (2 rows) + a
   button per category (/menu/<section>); parties (theme + drink list, one active, preview with
   ?party=<id>); admin drink editor with live preview, pump cards that save on change.
   A drink with several spirits is in each spirit's section (Long Island: Vodka, Tequila, Rum,
   Gin); spirits are recognised by name (`menu.SPIRIT_WORDS`), not ABV - old dbs have wrong ABVs.
   Hosted demo for colleagues: TrueNAS app `bartendro-demo` (port 8077, simulated pumps, resets
   on restart) public via Tailscale Funnel at https://truenas-scale.tail4c32e5.ts.net -
   docs/hosting.md + docs/project-notes.md. It runs a copy of the bot database committed as
   `deploy/demo-bot.db` (Kevin chose public over a NAS mount); refresh it from build/bartendro.db.
   It only gets new code (or a new demo-bot.db) when redeployed in TrueNAS. The static GitHub Pages demo was removed (don't
   bring it back unasked).
4. **Recipe database** - done 2026-10-01: `src/bartendro/data/classics.toml`, 62 drinks (7 non-alcoholic)
   written for this project (amounts are facts from classic/IBA specs; no copied text - the
   GitHub IBA datasets are scrapes of IBA's site + Wikipedia, so not bundled; TheCocktailDB must
   not be redistributed). **Kevin's rules:**
   - The bot only pours liquids. The guest does the rest by hand, before the pour (absinthe
     rinse, muddled mint/lime, ice) or after (dashes of bitters, shake/stir per `finish`).
     Ice is a before-the-pour checklist line (`[1, "fill", "Ice", "before"]`), never "Over ice"
     text; Ice starts on hand. Sazerac exists neat and "on the Rocks".
   - Each recipe line is pumped or added by hand, decided at pour time (`menu.resolve_line`):
     pumped if its ingredient is on a dispenser and the amount can be pumped (counted units
     convert via `PUMP_ML`: dash 0.9 ml...), else by hand if on hand. Bitters, absinthe, half
     and half can be either; `Ingredient.manual` = can never be pumped (ice, mint, wedges).
     Pumped amounts under option `min_pump_ml` go by hand if on hand. `step` = before/after;
     a pumped "before" line (absinthe rinse) pours in two stages with a Continue in between.
   - What's available by hand is tracked like the pumps: `Ingredient.on_hand` (Admin >
     Dispensers > On hand). Drinks only show when pumped bottles are loaded AND by-hand items
     are on hand.
   - Specific spirits stay specific: a recipe asking for Reposado Tequila needs reposado
     (plain Tequila won't do); a reposado bottle can make generic-Tequila drinks.
   Loader matches existing ingredients by name/alias, links brands (Kahlua -> Coffee Liqueur)
   and `generic` children; classic drinks pour their recipe total (size_ml). "One step away"
   (load a bottle / get something on hand) + `suggest N` bottle planner (beam search).
   - `Ingredient.alcoholic` = booze vs mixer (strength button scales booze). Drink section =
     `Drink.category` override, else the main spirit's top-level generic (menu.category_of).
   - Small pours: brainstorm only so far (plan file / chat 2026-10-02): independent size and
     strength-in-standard-drinks controls, sub-linear mixer scaling, minimum pour floor,
     per-pump calibration offset (ticks = a*ml + b). Needs real pumps to tune.
5. Install script / systemd service, NetworkManager hotspot, optional Chromium kiosk.

## Current state / next steps (2026-10-02)
- `modernize` pushed, 97 tests passing. Everything so far runs only against the simulator.
- Accented names (Jägermeister, Piña Colada, Strawberry Purée...) are fine UTF-8 in the old
  and new dbs; "�" only appears when a script prints to the Windows console. Check the bytes
  (`text_factory = bytes`) before "fixing" text, or set `PYTHONIOENCODING=utf-8`.
- Next: first real-hardware test (Pi + mini-router + 1-2 pumps, docs/pi-setup.md), then
  milestone 5 (install script, systemd, hotspot, kiosk for the Waveshare 10.1" screen), then
  small-pour tuning (ideas in milestone 4 notes) with real pumps and a scale.

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
- `web/theme.py` - party colours -> CSS variable overrides; logos in `<db dir>/uploads`.
- `showcase.py` - demo setup (bundled drinks, 15 bottles, demo party) for `bartendro-web --showcase`.
  The Dockerfile runs `--start-from /app/demo-bot.db` (fresh copy of deploy/demo-bot.db each start).
- `scripts/render_old_ui.py` - renders the original Python 2 templates to build/old-ui.
- `docs/project-notes.md` - decisions and findings (touchscreen, mini-router firmware, hosting,
  bot database); `docs/plans/` - approved plans.
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
