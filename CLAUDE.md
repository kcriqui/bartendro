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
2. Database: SQLAlchemy 2 + Alembic on SQLite; importer for old `bartendro.db` files.
3. Web app: FastAPI (or Flask 3) + htmx, phone-first (small bots have no screen), WebSocket
   pour status; port the state machine `ui/bartendro/fsm.py` + `mixer.py` logic.
4. Cocktail recipe database: ingredients separate from brands, pumpable vs. manual steps,
   units -> ml, "what can I make now". Check licences before bundling any dataset
   (TheCocktailDB data must not be redistributed; IBA list is a candidate seed).
5. Install script / systemd service, NetworkManager hotspot, optional Chromium kiosk.

## Layout
- `src/bartendro/hw/protocol.py` - packet format, CRC16, 7-bit packing (must match
  `firmware/common/packet.h` and `pack7.c`).
- `hw/router.py` (smbus2, bus 1, address 4), `hw/status_led.py` (gpiozero, BCM 24/23/25 =
  board pins 18/16/22), `hw/driver.py` (port of `ui/bartendro/router/driver.py` + the pour loop
  of `mixer._dispense_recipe`), `hw/simulator.py` (fake router + dispensers speaking the real
  protocol), `hw/connect.py` (open hardware or simulator).
- Port old logic faithfully; when changing behaviour, note it in a comment (e.g. discovery
  retries are now bounded).

## Testing
- `pip install -e ".[dev]"` then `pytest` (no hardware needed). `bartendro-hw --sim N ...`.
- Real hardware: never run pumps without Kevin's go-ahead (the CLI asks before pumping unless `-y`).
