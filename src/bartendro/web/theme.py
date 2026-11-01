"""Party themes: a party's colours as overrides of the CSS variables in static/style.css."""

from __future__ import annotations

import re

from ..db.models import Party

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# the original Bartendro look (style.css :root), what an empty party colour means
DEFAULTS = {"page": "#dadada", "frame": "#d9a180", "heading": "#005991", "button": "#fa6c19", "go": "#51a351"}


def valid(color: str) -> str:
    """'#rrggbb' (lower case) or '' for anything else."""
    color = (color or "").strip()
    return color.lower() if HEX.match(color) else ""


def _mix(color: str, other: str, amount: float) -> str:
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b, strict=True))


def lighter(color: str, amount: float = 0.3) -> str:
    return _mix(color, "#ffffff", amount)


def darker(color: str, amount: float = 0.15) -> str:
    return _mix(color, "#000000", amount)


def theme_css(party: Party | None) -> str:
    """CSS overriding the colour variables the party sets ('' when it sets none)."""
    if party is None:
        return ""
    rules = []
    if c := valid(party.color_page):
        rules.append(f"--page: {c};")
    if c := valid(party.color_frame):
        rules += [f"--frame: {c};", f"--frame-inner: {darker(c)};", f"--robot-tray: {lighter(c, .35)};"]
    if c := valid(party.color_heading):
        rules += [f"--heading: {c};", f"--edge: {lighter(c)};"]
    if c := valid(party.color_button):
        rules += [f"--btn-1: {lighter(c)};", f"--btn-2: {c};", f"--robot-drink: {lighter(c, .35)};",
                  f"--robot-dots: {c};"]
    if c := valid(party.color_go):
        rules += [f"--go-1: {lighter(c, .2)};", f"--go-2: {c};"]
    return ":root { " + " ".join(rules) + " }" if rules else ""
