# Architecture and code map

How the Python 3 app is built. Port old logic faithfully; when changing behaviour, say so in a
comment (e.g. "discovery retries are now bounded").

## Code map (`src/bartendro/`)

**Hardware (`hw/`)**
- `protocol.py` - packet format, CRC16, 7-bit packing; must match `firmware/common/packet.h` and
  `pack7.c` byte for byte.
- `router.py` - smbus2, I2C bus 1, address 4. `status_led.py` - gpiozero, BCM 24/23/25 = board pins
  18/16/22.
- `driver.py` - port of `ui/bartendro/router/driver.py` plus the pour loop of
  `mixer._dispense_recipe` (amounts under 20 ml go at half speed). Some firmware commands are ported
  but unused (comm_test, LED patterns, flush_saved_tick_count, set_liquid_level_thresholds) - keep.
- `simulator.py` - fake router + dispensers speaking the real protocol. `connect.py` - open the
  hardware or the simulator.

**Database (`db/`)**
- `models.py` (tables), `migrations/` (Alembic, no alembic.ini; run via `db.upgrade()`),
  `importer.py` (old bartendro.db -> new, read-only, keeps ids), `options.py` (typed settings;
  password hashed - `check_password` is unused while there's no login), `recipes.py` (loads
  classics.toml), `menu.py` (what can be made; parts -> ml with strength / tartness;
  `one_bottle_away`, `suggest_bottles` beam search; `display_name` for guest names).
- Recipes are in parts, scaled to the glass size at pour time, like the old app.
- `db.generation()` goes up on every commit (SQLAlchemy after_commit event).

**Bot (`bot.py`)** - port of mixer.py + fsm.py. One action at a time (a lock; `BusyError`), drink ->
dispenser plan, shots, test dispense, pump runs, clean (all / left #5-#11 / right #1-#4,#12-#15 on a
15-pump bot, or one pump), levels, CURRENT_SENSE / ERROR states + reset. `background=True` runs the
pumping in a thread; listeners get status / pour events.

**Web (`web/`)** - `create_app(bot)`: pages, JSON API under `/api`, WebSocket `/ws`. `server.py` =
`bartendro-web [--sim N] [--sim-view-only] [--dev] [--start-from FILE] [--access-log FILE] [--showcase] [--banner ...]
[--no-uploads]`, port 8080. `templates/`, `static/` (style.css, app.js, led-font.js), `theme.py`
(party colours -> CSS variables; logos in `<db dir>/uploads`).

**Simulator page (`/sim`, 2026-10-31)** - only when the pumps are simulated (`--sim N`): the
dispensers 3 wide (Kevin's bots are 3 x 5, `SIM_COLUMNS`) with their LEDs animated using the
firmware's own patterns and timing (copied from `firmware/dispenser/led.c` into `static/sim.js`),
rotors turning while pumping, ml for the current run and in total (ticks / the pump's
calibration), and the liquid level. Open it in a second window next to the menu: the "Sim" link in
the top bar (shown only when simulating) opens it in a window of its own. Fed by the WebSocket
`/ws/sim` (state sent when it changes, at most every 0.1 s). Controls: **Jam** (the pump stalls
when it runs, like a blocked tube: current sense, blinking red, the bot asks for a reset) and
level buttons (Full / Low / Out; only used with "use liquid level sensors" on).
`--sim-view-only` hides them and refuses `/api/sim/...` (for a public copy; the hosted demo keeps them). The simulator
(`hw/simulator.py`) behaves like the firmware here: a stalled dispenser ignores motor and LED
commands until the router's reset restarts it.

**Other:** `config.py` (per-bot TOML, `docs/bartendro.example.toml`), `cli.py` (`bartendro-hw`),
`dbcli.py` (`bartendro-db`), `showcase.py` (bundled drinks + 15 bottles + a demo party for
`--showcase`).

**Scripts:** `render_old_ui.py` (renders the original Python 2 templates to build/old-ui),
`trace_robot.py` (partyrobot.png -> `_robot.html`), `check_demo_log.py` / `demo-log-summary.js`
(demo logs, see [hosting.md](hosting.md)). `scripts/user_button.py`, `restartd.conf`,
`start_bartendro.sh` are the original project's (Python 2) - not part of the rewrite.

## Web app

- **Stack:** FastAPI + Jinja templates + ~400 lines of plain JS. No htmx or CDN: the bot's hotspot
  has no internet. WebSocket pushes pour status to every screen. **No login** (Kevin's call): anyone
  on the bot's WiFi can use admin. Kiosk on localhost and phones at the same time.
- **Look:** the original Bartendro theme by default (colours from `ui/content/static/css/bartendro.css`,
  logo and robot images).
- **Menu:** "the essentials" (2 rows of 2) + a button per category (`/menu/<section>`) + All drinks.
  A drink with several spirits is in each spirit's section (Long Island: Vodka, Tequila, Rum, Gin);
  spirits are recognised by name (`menu.SPIRIT_WORDS`), not ABV - old dbs have wrong ABVs.
- **Guest names:** guests see "Tommy's Margarita" for "Margarita, Tommy's" (`menu.display_name`, the
  `guest` template filter, `web.guest_event` for pour messages); admin keeps the real names and
  menus sort by them ([recipes.md](recipes.md#names)).
- **Parties** (Admin > Parties): theme colours, title, welcome line, optional banner image, drink
  list (one active at a time; preview with `?party=<id>`), robot (`Party.robot`) and LED sign
  (`Party.marquee`).
- **Menu art:** the party's banner on top (if any) and the robot big and faded behind the menus
  (below the banner), on the menu, section and shots pages (shots: on the right).
  - `templates/_robot.html` - the original `partyrobot.png` traced to inline SVG by
    `scripts/trace_robot.py` (re-run it, don't hand-edit). Coloured by `--robot`, `--robot-tray`,
    `--robot-drink`, `--robot-dots`; a party's colours set them (tray <- frame, drink + dots <-
    button; `web/theme.py`).
  - `templates/_bar2d2.html` - the Bar2D2 robot, hand-drawn SVG, `--b2-*` colours.
  - `templates/_robot_bg.html` picks one per party (`ROBOTS` in web: "" = party robot, "bar2d2").
  - LED sign: rainbow dot-matrix text scrolling at the top of the menu, drawn on a canvas by app.js
    `ledSign` (16 LED rows, dark LEDs cached, redraws only when the text moves) with the 9x15 bold
    font in `static/led-font.js`.
  - Reference pictures: `docs/images/`. Party banner files: `deploy/uploads/` (copied by
    `--start-from`).
- **Admin:** drink editor with live preview, dispenser cards that save on change (bottle,
  calibration, Empty line / Prime / Test / Clean, each with a confirm), on-hand list, "one step
  away", bottle planner, parties, options, pour log.
- **Crawlers:** `/robots.txt` disallows everything and every page has `noindex, nofollow`
  (2026-10-06, after ClaudeBot crawled the public demo's admin pages).
- **Access log:** `--access-log FILE` writes one JSON line per request / WebSocket (rotated 5 MB x
  5); the container's own health check isn't logged.

## Speed and cores (2026-10-02)

- Guest pages ~6 ms on Kevin's Ryzen 9 7945HX (was ~37 ms). A Pi 4 is maybe 8-10x slower than that
  PC. The makeable-drinks list is cached until the next commit (`db.generation()`) or 60 s;
  templates are read once unless `--dev` (checking them per request was ~60% of the time on the NAS
  share); responses are gzipped (menu pages ~35-40 KB -> ~10 KB).
- **One process, about one core, by design.** uvicorn runs one worker; page requests use a small
  thread pool and pours run in a background thread, but Python runs one thread at a time and the
  work is mostly waiting on the serial / I2C link. Several processes would break the pour lock,
  the shared status, the WebSocket broadcast and the cache, and could fight over the serial port.
  On a Pi 4 the other cores go to Chromium (kiosk), the hotspot and the OS. If heavy computing ever
  appears, move it to a separate worker process, not the one that owns the pumps.
- Not worth doing before measuring on a Pi: making the ~32 KB inline robot SVG a cached file;
  caching "what can be made" on admin pages.

## Schema changes

Edit `models.py`, then `bartendro-db --db scratch.db revision -m "..."`, review the generated file
(add `server_default` for new NOT NULL columns), rename it to `000N_<name>.py` with revision
"000N" like the others, and bump the latest-version checks in `tests/test_db.py`
(test_upgrade_keeps_data, test_dbcli). `test_migrations_match_models` fails until the migration
exists; `test_upgrade_keeps_data` checks a populated db survives. Migrations run with foreign keys
off in one transaction (`db/migrations/env.py`) - batch mode drops and recreates tables, which
otherwise breaks or cascade-deletes referencing rows. Enum columns store values
(`values_callable`); keep `str` + `Enum` (not `StrEnum`: `str()` of the members differs).
