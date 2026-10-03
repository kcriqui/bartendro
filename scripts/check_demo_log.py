"""Check the hosted demo's access log for trouble (run twice a day by a scheduled Claude task).

Reads build/demo-logs/access.log (+ rotated .1-.5), written by the demo container
(bartendro-web --access-log), looks at what's new since the last run (state in
build/demo-logs/checker-state.json) and prints a report with Pacific times. First line:

    ALERT: ...   something worth a look -> the task sends Kevin a notification
    OK: ...      nothing unusual (routine scanner noise is only summarised)

Exit code 1 on ALERT. --all: look at the whole log, not just what's new; --dry-run: don't update
the state file.

Raises an alert for:
  - a request that wasn't a 404 to a path the app doesn't serve (a probe that got somewhere)
  - server errors (5xx)
  - changes (POST, success) from outside Kevin's LAN / tailnet - someone using admin or pumps
  - a flood: one address making more than FLOOD_PER_MIN requests in a minute
  - the log missing or not written for LOG_STALE_HOURS (logging broken or the demo down)
Scanners that only collect 404s are normal on a public URL: counted, not alerted.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "build" / "demo-logs"
STATE = LOG_DIR / "checker-state.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
FLOOD_PER_MIN = 120
LOG_STALE_HOURS = 36
OURS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                          "10.0.0.0/8", "100.64.0.0/10")]  # LAN, Docker, tailnet
APP_PATHS = re.compile(r"^/($|menu(/|$)|drink/|shots$|admin(/|$)|api/|static/|uploads/|ws$|favicon\.ico$)")
PROBE = re.compile(r"\.(php|asp|aspx|jsp|cgi|env|git|sql|bak|zip|tar|gz|xml|yml|yaml|ini|log|pwd)\b|"
                   r"/(wp-|\.well-known/|_ignition|actuator|cgi-bin|vendor/|admin\.php|phpmyadmin)", re.I)


def ours(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in OURS)


def pacific(t: datetime) -> str:
    return t.astimezone(PACIFIC).strftime("%b %d %I:%M %p %Z").replace(" 0", " ")


def read_log() -> list[dict]:
    entries = []
    for f in LOG_DIR.glob("access.log*"):        # the current file and the rotated ones
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
                e["time"] = datetime.fromisoformat(e["t"])
                entries.append(e)
            except (ValueError, KeyError):
                continue
    entries.sort(key=lambda e: e["time"])
    return entries


def check(entries: list[dict], since: datetime | None, now: datetime) -> tuple[list[str], list[str]]:
    alerts, notes = [], []
    if not entries:
        return [f"no access log in {LOG_DIR} - is the demo running, with its log folder mounted?"], notes
    newest = entries[-1]["time"]
    if now - newest > timedelta(hours=LOG_STALE_HOURS):
        alerts.append(f"nothing logged since {pacific(newest)} - demo down or logging broken? "
                      f"(the demo's own health check isn't logged, so a quiet day can do this too)")
    new = [e for e in entries if since is None or e["time"] > since]
    if not new:
        notes.append("no new requests since the last check")
        return alerts, notes

    visitors = {e["ip"] for e in new if not ours(e["ip"])}
    notes.append(f"{len(new)} new requests from {len({e['ip'] for e in new})} addresses "
                 f"({len(visitors)} outside your LAN/tailnet), {pacific(new[0]['time'])} - {pacific(new[-1]['time'])}")

    got_somewhere = [e for e in new if not APP_PATHS.match(e["path"].split("?")[0]) and e["status"] not in (404, 405)
                     and e["m"] != "WS"]
    for e in got_somewhere[:10]:
        alerts.append(f"{e['ip']} {e['m']} {e['path']} -> {e['status']} at {pacific(e['time'])} "
                      f"(not an app page, but not a 404)")
    errors = [e for e in new if e["status"] >= 500]
    if errors:
        by = Counter(f"{e['m']} {e['path'].split('?')[0]}" for e in errors)
        alerts.append(f"{len(errors)} server error(s): " + ", ".join(f"{k} x{n}" for k, n in by.most_common(5)))
    changes = defaultdict(list)
    for e in new:
        if e["m"] in ("POST", "PUT", "PATCH", "DELETE") and e["status"] < 400 and not ours(e["ip"]):
            changes[e["ip"]].append(e)
    for ip, es in changes.items():
        what = Counter(e["path"].split("?")[0] for e in es)
        alerts.append(f"{ip} changed things on the demo {len(es)}x, {pacific(es[0]['time'])} - "
                      f"{pacific(es[-1]['time'])}: " + ", ".join(f"{p} x{n}" for p, n in what.most_common(6)))
    per_min = Counter((e["ip"], e["time"].replace(second=0, microsecond=0)) for e in new)
    floods = {ip: n for (ip, _), n in per_min.items() if n > FLOOD_PER_MIN}
    for ip, n in floods.items():
        alerts.append(f"{ip} made {n} requests in one minute (flood / scraping?)")

    scanners = defaultdict(int)
    for e in new:
        if e["status"] in (404, 405) and (PROBE.search(e["path"]) or not APP_PATHS.match(e["path"].split("?")[0])):
            scanners[e["ip"]] += 1
    if scanners:
        notes.append(f"routine scanning: {sum(scanners.values())} probes from {len(scanners)} address(es), "
                     f"all 404 - " + ", ".join(f"{ip} x{n}" for ip, n in sorted(scanners.items(), key=lambda x: -x[1])[:8]))
    people = defaultdict(lambda: {"pages": 0, "live": 0, "first": None, "last": None})
    for e in new:
        if e["ip"] in scanners or ours(e["ip"]):
            continue
        p = people[e["ip"]]
        p["first"] = p["first"] or e["time"]
        p["last"] = e["time"]
        if e["m"] == "WS":
            p["live"] += 1
        elif e["m"] == "GET" and not re.match(r"^/(static|api|uploads)/", e["path"]):
            p["pages"] += 1
    visits = {ip: p for ip, p in people.items() if p["live"] or p["pages"] > 2}
    for ip, p in sorted(visits.items(), key=lambda x: x[1]["first"]):
        notes.append(f"visitor {ip}: {p['pages']} pages, {p['live']} live connections, "
                     f"{pacific(p['first'])} - {pacific(p['last'])}")
    return alerts, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all", action="store_true", help="check the whole log, not just what's new")
    ap.add_argument("--dry-run", action="store_true", help="don't remember this run")
    args = ap.parse_args(argv)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    since = None if args.all or "last" not in state else datetime.fromisoformat(state["last"])
    now = datetime.now(timezone.utc)
    entries = read_log()
    alerts, notes = check(entries, since, now)
    period = f"since {pacific(since)}" if since else "whole log"
    if alerts:
        print(f"ALERT: {len(alerts)} thing(s) on the Bartendro demo worth a look ({period})")
        for a in alerts:
            print(f"  ! {a}")
    else:
        print(f"OK: nothing unusual on the Bartendro demo ({period})")
    for n in notes:
        print(f"  - {n}")
    if not args.dry_run and entries:
        STATE.write_text(json.dumps({"last": entries[-1]["t"], "checked": now.isoformat(timespec="seconds")}))
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
