"""Render the original (Python 2) Bartendro pages to static HTML, for comparing looks.

    py scripts/render_old_ui.py            # -> build/old-ui/{index,drink,shots}.html + static/
    py -m http.server 8090 -d build/old-ui # then open http://localhost:8090/index.html

Uses the original templates (ui/content/templates) and assets (ui/content/static) unchanged,
with sample data from ui/bartendro.db.default (the 15 dispensers as set up there).
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ui" / "content" / "templates"
STATIC = ROOT / "ui" / "content" / "static"
DB = ROOT / "ui" / "bartendro.db.default"
OUT = ROOT / "build" / "old-ui"

OPTIONS = NS(i_am_shotbot=0, use_shotbot_ui=1, show_feeling_lucky=0, turbo_mode=0, show_size=1,
             show_strength=1, show_taster=1, drink_size=150, taster_size=30, strength_steps=2,
             metric=0, shot_size=30, must_login_to_dispense=0)


def load_drinks():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    have = {r[0] for r in c.execute("SELECT booze_id FROM dispenser")}
    drinks = []
    for d in c.execute("SELECT d.id, d.desc, d.popular, n.name FROM drink d JOIN drink_name n "
                       "ON n.id = d.name_id ORDER BY n.name"):
        ings = [dict(name=r["name"], id=r["id"], parts=r["value"], type=r["type"])
                for r in c.execute("SELECT b.name, b.id, db.value, b.type FROM drink_booze db "
                                   "JOIN booze b ON b.id = db.booze_id WHERE db.drink_id = ?", (d["id"],))]
        if ings and all(i["id"] in have for i in ings):
            drinks.append(NS(id=d["id"], desc=d["desc"], popular=d["popular"], is_lucky=0,
                             name=NS(name=d["name"]), ingredients=ings))
    shots = [NS(id=r["id"], name=r["name"], desc=r["desc"]) for r in c.execute(
        "SELECT b.id, b.name, b.desc FROM dispenser x JOIN booze b ON b.id = x.booze_id ORDER BY x.id")]
    return drinks, shots


def main() -> None:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    drinks, shots = load_drinks()
    top = [d for d in drinks if d.popular]
    other = [d for d in drinks if not d.popular]
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "static").exists():
        shutil.rmtree(OUT / "static")
    shutil.copytree(STATIC, OUT / "static")
    common = dict(options=OPTIONS, title="Bartendro",
                  current_user=NS(is_authenticated=lambda: False))
    pages = {
        "index.html": env.get_template("index").render(
            top_drinks=top, other_drinks=other, lucky_drink_id=0, error_message="", **common),
        "drink.html": env.get_template("drink/index").render(
            drink=top[0], can_make=1, can_change_strength=1, show_sweet_tart=1, is_custom=0, go=0,
            **common),
        "shots.html": env.get_template("shots").render(num_shots_ready=len(shots), shots=shots, **common),
    }
    for name, html in pages.items():
        # links point at the old server's routes; keep them clickable between these pages
        html = html.replace('href="/"', 'href="/index.html"').replace('href="/shots"', 'href="/shots.html"')
        (OUT / name).write_text(html, encoding="utf-8")
        print(OUT / name)


if __name__ == "__main__":
    main()
