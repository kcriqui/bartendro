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
the typos "Margarita, Repasado", "Margarita, Pinapple", "Daquari, Santa Ana" plus the near-duplicates
Corpse Reviver #2 / No. 2 and Rum, Light / Rum, White (not asked).

Parties in the bot database (both inactive): Halloween (from the showcase) and **Yurtville Weekend**
(Kevin's party at the end of July at Yurtville, in the redwoods near Santa Cruz): US Forest Service
colours (green #1f4d2b, brown #5b3a1e, yellow), logo `deploy/uploads/yurtville.svg` (banjo + lasers +
redwoods, drawn for this project; also in build/uploads), no drink list yet = every drink.
