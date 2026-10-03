import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_demo_log", Path(__file__).resolve().parent.parent / "scripts" / "check_demo_log.py")
cdl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdl)

NOW = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)


def entry(minutes_ago, ip, m, path, status):
    t = NOW - timedelta(minutes=minutes_ago)
    return {"t": t.isoformat(), "time": t, "ip": ip, "m": m, "path": path, "status": status}


def test_routine_scanning_and_visitors_are_ok():
    entries = [entry(50, "45.148.10.8", "GET", "/.git/config", 404),
               entry(40, "81.171.72.93", "GET", "/wp-config.php", 404),
               entry(30, "73.222.53.150", "GET", "/", 200), entry(30, "73.222.53.150", "WS", "/ws", 101),
               entry(20, "100.77.157.38", "POST", "/admin/party/3", 303)]   # Kevin's tailnet
    alerts, notes = cdl.check(entries, None, NOW)
    assert alerts == []
    assert any("routine scanning: 2 probes" in n for n in notes)
    assert any(n.startswith("visitor 73.222.53.150") for n in notes)


def test_alerts():
    entries = [entry(50, "74.82.58.3", "POST", "/api/pumps/run", 202),     # an outsider changing things
               entry(40, "9.9.9.9", "GET", "/.env", 200),                  # a probe that got somewhere
               entry(30, "8.8.8.8", "GET", "/admin", 500)]
    entries += [entry(10, "1.2.3.4", "GET", "/", 200) for _ in range(cdl.FLOOD_PER_MIN + 1)]
    alerts, _ = cdl.check(entries, None, NOW)
    text = "\n".join(alerts)
    assert "74.82.58.3 changed things" in text and "/api/pumps/run" in text
    assert "9.9.9.9 GET /.env -> 200" in text
    assert "1 server error(s)" in text
    assert "1.2.3.4 made 121 requests in one minute" in text


def test_only_new_entries_and_stale_log():
    old = [entry(60 * 48, "73.222.53.150", "POST", "/admin/options", 303)]
    alerts, notes = cdl.check(old, old[0]["time"], NOW)
    assert any("nothing logged since" in a for a in alerts)   # 48 h of silence
    assert "no new requests since the last check" in notes
    assert cdl.check([], None, NOW)[0][0].startswith("no access log")
