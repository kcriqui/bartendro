"""Export a static demo of the web app for GitHub Pages (like infrasound-monitor-site).

    py scripts/export_site.py                       # -> build/pages/bartendro/...
    py -m http.server 8000 -d build/pages           # try it at http://localhost:8000/bartendro/
    py scripts/export_site.py --publish             # push build/pages/bartendro to the gh-pages branch

The pages are rendered by the real app (demo=True) against a showcase database: the bundled
recipes, 15 bottles on simulated pumps, the usual by-hand items and a demo party. Each drink's
pour plan is exported at every strength so static/demo.js can answer the drink page's API
calls; pours play the same pop-ups as on the bot. Nothing is saved.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from bartendro import showcase
from bartendro.bot import Bot
from bartendro.db import options
from bartendro.db.models import Drink, Ingredient, Party
from bartendro.hw.driver import Driver
from bartendro.hw.simulator import SimBus
from bartendro.web import create_app

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "pages"
BASE = "/bartendro/"  # GitHub Pages path: kcriqui.github.io/bartendro/
def rewrite(html: str, base: str) -> str:
    """Root-relative links -> relative to <base>, so the site works under any path."""
    html = re.sub(r'(href|src|action)="/(?!/)', r'\1="', html)
    html = re.sub(r'href="\?party=(\d+)"', lambda m: 'href="party/"' if m.group(1) != "0" else 'href="./"', html)
    html = re.sub(r'href="(admin/plan)\?pumps=\d+"', r'href="\1/"', html)
    return html.replace("<head>", f'<head>\n<base href="{base}">', 1)  # after the rewrite above


def out_file(url: str, root: Path) -> Path:
    path = url.split("?")[0].strip("/")
    return root / path / "index.html" if path else root / "index.html"


def export(base: str = BASE) -> Path:
    site = OUT / base.strip("/")
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp())
    sessions = showcase.build(tmp / "showcase.db")
    bus = SimBus.with_dispensers(15)
    driver = Driver(bus.serial, bus.router)
    driver.discover()
    bot = Bot(driver, sessions)
    bot.start()
    uploads = tmp / "uploads"
    with TestClient(create_app(bot, demo=True, uploads=uploads)) as client:
        with sessions() as s:
            drink_ids = list(s.scalars(select(Drink.id)))
            ing_ids = list(s.scalars(select(Ingredient.id)))
            party_ids = list(s.scalars(select(Party.id)))
            steps = int(options.get(s, "strength_steps"))
        menu = client.get("/").text
        urls = ["/", "/menu/all", *sorted(set(re.findall(r'href="(/menu/[^"]+)"', menu))),
                "/shots", "/admin", "/admin/drinks", "/admin/drink/new", "/admin/ingredients",
                "/admin/ingredient/new", "/admin/options", "/admin/parties", "/admin/party/new",
                "/admin/plan", "/admin/log",
                *[f"/drink/{i}" for i in drink_ids], *[f"/admin/drink/{i}" for i in drink_ids],
                *[f"/admin/ingredient/{i}" for i in ing_ids], *[f"/admin/party/{i}" for i in party_ids]]
        for url in urls:
            r = client.get(url)
            assert r.status_code == 200, (url, r.status_code)
            f = out_file(url, site)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(rewrite(r.text, base), encoding="utf-8")
        # the party preview, then back to the plain menu
        f = site / "party" / "index.html"
        f.parent.mkdir(parents=True)
        f.write_text(rewrite(client.get(f"/?party={party_ids[0]}").text, base), encoding="utf-8")
        client.get("/?party=0")
        # pour plans for the drink pages (static/demo.js)
        for i in drink_ids:
            d = site / "api" / "drink" / str(i)
            d.mkdir(parents=True)
            for strength in range(-steps, steps + 1):
                r = client.get(f"/api/drink/{i}/plan", params={"strength": strength})
                (d / f"plan{strength}.json").write_text(json.dumps(r.json()), encoding="utf-8")
    shutil.copytree(ROOT / "src" / "bartendro" / "web" / "static", site / "static")
    if uploads.exists() and any(uploads.iterdir()):
        shutil.copytree(uploads, site / "uploads")
    (site / ".nojekyll").write_text("")
    print(f"{len(urls) + 1} pages, {len(drink_ids)} drinks -> {site}")
    return site


def publish(site: Path) -> None:
    """Commit the site as the only content of the gh-pages branch and push it."""
    remote = subprocess.check_output(["git", "-C", str(ROOT), "remote", "get-url", "origin"], text=True).strip()
    work = Path(tempfile.mkdtemp())
    shutil.copytree(site, work, dirs_exist_ok=True)

    def git(*args):
        subprocess.run(["git", "-C", str(work), *args], check=True)
    git("init", "-q", "-b", "gh-pages")
    git("add", "-A")
    git("commit", "-q", "-m", "Static demo of the Bartendro web app (scripts/export_site.py)")
    git("push", "-f", remote, "gh-pages")
    print(f"pushed to {remote} gh-pages")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=BASE, help=f"URL path the site is served under (default {BASE})")
    ap.add_argument("--publish", action="store_true", help="push the result to the gh-pages branch")
    args = ap.parse_args()
    site = export(args.base if args.base.endswith("/") else args.base + "/")
    if args.publish:
        publish(site)
    return 0


if __name__ == "__main__":
    sys.exit(main())
