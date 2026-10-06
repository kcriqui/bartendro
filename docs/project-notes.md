# Project notes

Decisions and findings that aren't obvious from the code. Kept here (on the NAS, in the repo)
so they're the same from every PC. Plans live in [plans/](plans/).

## Touchscreen for the 15-pump bots (2026-10-01)

Kevin plans to buy the **Waveshare 10.1" capacitive touch display with aluminium case** (1280x800,
bonded glass, HDMI + USB-C; Amazon ASIN B0C2V6J4F9, $115.99). He prefers HDMI + USB touch over
DSI/GPIO displays. On a Pi 3B+/4: HDMI for video, the display's USB-C port to a Pi USB-A port for
touch (the Pi can't send video over USB-C); power the display separately.

Architecture: one web app; a Chromium kiosk on localhost AND phones over WiFi at the same time.
The server owns the state machine / pour lock (one drink at a time); the WebSocket pushes pour
status to every screen. Why: the WiFi web UI was always unreliable on the old bots, so the local
screen must not depend on WiFi. Kiosk setup belongs to milestone 5. No login for anyone (Kevin's
call); the hosted demo is open too.

## Mini-router (3-port) vs 15-port router firmware (2026-10-01)

Firmware unchanged since 2013. The first hardware test is planned on a Pi + mini-router + 1-2
dispensers, and the small bots (margaritabot, ...) use the mini-router. Short version is also in
[pi-setup.md](pi-setup.md#mini-router-3-port-board).

**Same on both:** I2C address 4; commands select (<15), RESET 255, SYNC_ON 251 / SYNC_OFF 252
(neither implements PING 253 / COUNT 254); reset = 5 sync pulses, 10 ms reset pulse, 2 s wait; the
Pi's TX goes to all dispensers, only dispenser->Pi RX is switched. The Python code needs no changes
(`--ports 3`).

**Differences** (the mini-router never got the Aug 2013 router rewrite, Ray Lee's
partyrobotics/bartendro#77, commits e9ef45f/d7578c6):
- Ports 0-2 only (0=PD4, 1=PD3, 2=PD0). Selecting 3-14 is accepted but relays nothing.
- Echo: the router copies current pin levels on any pin change ("always echo"). The mini-router
  compares against a cached level per pin (pcint16/19/20) that only updates while that port is
  selected, so it can go stale -> missed edge -> inverted/garbled bytes.
- Startup: the router's setup() initialises the output lines from the inputs (dabf672). The
  mini-router never sets PB0 (TX to Pi): it starts LOW (a break) with cache 0 while idle is high,
  so the first reply after a reset can be garbled. discover()'s 3-identical-bytes check and 5
  retries should absorb it.
- The mini-router enables the pull-up on PB1 (Pi RX input; its comment wrongly says "B1 high").
- The pre-2013 ui driver had a "mini-router mapping hack" rotating ids for 3 dispensers, removed
  in b6773ea when the mini-router firmware was added. Confirm physical port 0 with `discover`.

If a mini-router bot shows "inconsistent, retrying", short replies, lost ACKs or is_dispensing
noise that the 15-port router doesn't, suspect the echo scheme first. Fix: port router.c's
echo_dispenser()/echo_rpi() and the startup line init (~20 lines, 3-entry pin table); needs
avr-gcc + an AVR ISP programmer, so only if testing shows problems. Kevin's board may not even run
the repo's version of the firmware.

## Hosted demo bot (2026-10-02)

Fully working copy with simulated pumps for colleagues outside the LAN ([hosting.md](hosting.md)):
TrueNAS custom app **`bartendro-demo`** (Install via YAML with `deploy/truenas-demo.yaml`, entered
as one-line JSON because the TrueNAS editor auto-indents), NAS port **8077**, made public by Kevin
with `tailscale funnel --bg 8077` in the TrueNAS tailscale app's shell. One shared bot, no
password (Kevin's choice). Restart the app to reset it.
Since 2026-10-02 it runs a copy of the bot database (`deploy/demo-bot.db`, committed - Kevin chose
that over mounting build/bartendro.db from the NAS, so his recipes are public on GitHub) instead of
the showcase. Refresh: copy build/bartendro.db over it, push, redeploy.
Public URL: **https://truenas-scale.tail4c32e5.ts.net** (LAN: http://truenas:8077).
Watching it (2026-10-03): access log on the NAS + `scripts/check_demo_log.py`, run by the scheduled
Claude task bartendro-demo-watch on Kevin's home PC only (he chose that over a NAS cron job; runs
missed while the PC is off happen at the next app launch, nothing is lost). Kevin: if the reports
show a lot of scanners, password-protect the demo site (shared password, demo only - the bots stay
login-free).
- New code reaches it only when the app is edited/redeployed in TrueNAS (`pull_policy: build`).
- The first build failed with a transient "SSL connection timeout" fetching from GitHub; a retry
  worked. If it recurs, publish a prebuilt image (GHCR) instead of building on the NAS.
- NAS access from the PC is the TrueNAS web UI (Kevin's Chrome); no SSH. App logs need root:
  Kevin runs `sudo tail -n 40 /var/log/app_lifecycle.log`. Funnel / ACL changes are Kevin's to make.
- The static GitHub Pages demo was removed on 2026-10-02 (gh-pages deleted). GitHub's API refused
  to switch Pages off for this fork; with no branch it serves nothing.

## Bot database (2026-10-02)

`build/bartendro.db` (on the NAS, git-ignored) is the database for Kevin's bots: the recipes and
bottles from `lunarville-bartendro.db` (an old-style bot database; no logs, no settings) plus the
bundled drinks. Built by `build/merge_lunarville.py`. Kevin's choices: for same-name drinks keep
Lunarville's recipe (Black Russian, Cape Cod, Whiskey Sour, Greyhound, Cosmopolitan, Kamikaze,
Fuzzy Navel, Amaretto Sour, Hairy Navel, White Russian, Screwdriver; Tequila Sunrise was identical)
except Manhattan (web version). "Empty" bottles became empty pumps (#3, #15); "Tequia, Anejo"
renamed Anejo Tequila; Patron Citronge and Pierre Ferrand dry curacao are brands of Triple Sec,
the Bourbon variants brands of Whiskey, "Lime Juice, Persian" a brand of Lime Juice.
Settings are the defaults except drink_size = 100 ml (as at Lunarville; its taster was 20).
Old default database (`ui/bartendro.db.default`): 59 of its 83 drinks are identical in Lunarville and
9 more are there renamed / reworked (Lunarville renamed drinks "Margarita, SND" style and buries
retired ones with a "zzz" prefix - so compare by description and recipe, not just name). The 6
it really lacked were added from the default db (`build/add_default_missing.py`): Top Shelf
Margarita, Authentic Margarita, Dirty Sanchez, Baileys only!, White Catalan, Barcelona Mudslide
(+ Ratafia). 216 drinks (200 after the cleanup below).
Renamed to Kevin's "Tequila, Anejo" style (`build/rename_and_parties.py`, backup
`build/bartendro.before-rename.db`); merged duplicates that showed up: Reposado Tequila ->
Tequila, Reposado, Rum - Dark -> Rum, Dark, Bitters - Angostura -> Bitters, Angostura. Left as is:
juices/syrups/purees ("Lime Juice, Persian" style already), liqueurs, Kevin's z/zzz names ("z(na) " was removed: the 11 drinks are all in the Non-alcoholic
section now). Deleted (Kevin): the 8 "Z Shot of ..." drinks (the Shots page does that) and 8
retired zzz drinks (4 identical to live ones, "zzz delete me", the old Margarita, Lemon /
Pomegranate and Manhattan, Jack's). The last two lost their prefix: "Cosmopolitan, Count Drac's"
(still switched off) and "Cucumber Gin & Pim's" (on). 200 drinks, and
the near-duplicates
Corpse Reviver #2 / No. 2 and Rum, Light / Rum, White (not asked). Typos fixed later (Kevin): Margarita, Pineapple / Reposado,
Daiquiri, Santa Ana.

Parties in the bot database (both inactive): Halloween (from the showcase) and **Yurtville Weekend**
(Kevin's party at the end of July at Yurtville, in the redwoods near Santa Cruz): US Forest Service
colours (green #1f4d2b, brown #5b3a1e, yellow), logo `deploy/uploads/yurtville.svg` (banjo + lasers +
redwoods, drawn for this project; also in build/uploads), no drink list yet = every drink.

## Party robot artwork (2026-10-02)

Kevin's reference picture of the happy party robot: [images/happy-robot.avif](images/happy-robot.avif)
(the same drawing as the Kickstarter page's robot). The robot on the menus is traced from the repo's
own copy, `ui/content/static/images/partyrobot.png`, by `scripts/trace_robot.py` - a hand-drawn
copy wasn't close enough. It sits big behind the menus (40% opacity, outlines lighter than the
colours); party colours repaint it: tray <- frame colour, drink and dots <- button colour.

## Bar2D2 party (2026-10-02)

Kevin's third party theme: **Bar2D2**, a robot that's a cross between R2-D2 and a Dalek - astromech
dome with radar eye and blue panels, Dalek eyestalk, ear lamps, slatted shoulders and skirt, with
peristaltic pumps (all the same size, spaced like a Dalek's bumps - the count doesn't matter) where the
bumps are. Like the party robot it balances a tray with a 3D martini glass (the party robot's orange,
#f3a96c) - on the Dalek's plunger - and has bubbles, chunky outlines, a smile and a lean. The other arm
is an olive pick. Drawn by hand as SVG: `src/bartendro/web/templates/_bar2d2.html`.
Party settings: robot = bar2d2, R2 colours (frame/buttons #2a5db0, headings #1f3f7a, pour #d8402f), and
instead of a banner image an **LED sign** - a scrolling rainbow dot-matrix with settable text, default
"My Name is Bar2D2, I think I love you ;)". Both are per-party settings any party can use (Admin >
Parties: "Robot behind the menus", "LED sign text"; migration 0007).
LED sign font (2026-10-02): **9x15 bold**, one of the public-domain X11 misc-fixed bitmap fonts as
shipped for LED panels by https://github.com/hzeller/rpi-rgb-led-matrix (fonts/9x15B.bdf), one LED per
font pixel on the 16-row board (`static/led-font.js`). Kevin picked it from a side-by-side of 7x14,
8x13, 9x15 (regular + bold) and Helvetica 12 (scratch: build/fonts/compare.html, bdf2json.py). Tried
before and rejected: canvas text (strokes too thick), the Adafruit GFX 5x7 (boring), 5x7 doubled with
Scale2x (too smoothed). Pixel Operator (CC0, 16 px) wasn't reachable to try.

## Pi model and performance (2026-10-02)

Measured on Kevin's PC (Ryzen 9 7945HX), after the speed-up in 6dc26c6: guest pages ~6 ms, ~100 MB
for the Python app; pages are gzipped to ~10-12 KB. Estimates below are rough (Python on a Pi 4 is
maybe 8-10x slower than that PC, a Pi 3B+ another 2-3x) - measure on the real boards.

- **Pi 4, 2 GB** - recommended for the 15-pump bots with the Waveshare 10" kiosk: the Chromium
  kiosk takes 300-500 MB; menu pages maybe ~50-60 ms. 1 GB works if the kiosk is the only thing
  running (minimal desktop or bare kiosk, zram instead of swap on the SD card).
- **Pi 3B+, 1 GB** - fine as a server only (phones over the hotspot): maybe ~100-150 ms per page,
  ~100 MB app + hotspot. Good for the 3-pump bots (margaritabot...). Marginal for the kiosk:
  Chromium plus the weak GPU at 1280x800 - expect ~1 s page changes and jank, most likely in the
  animated LED sign and the faded robot background layered under it. If a Pi 3B+ must drive the
  screen, milestone 5 could add a "light kiosk" mode: Cog/WPE WebKit or a pared-down Chromium,
  the LED sign at a lower frame rate, no robot background on that screen.
- Pumping speed is set by the router/I2C, not the Pi.
- SD card (both): power cuts are the risk, not wear - good A1/A2 card, SQLite in WAL mode, logs
  in RAM, no swap on the card, ideally a read-only root with the database on a small data
  partition, and a database backup at start-up (milestone 5 install script).
- To check on real hardware: page timings (small benchmark script), Chromium memory over an
  evening, steady frame rate of the LED sign.

