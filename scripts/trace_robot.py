"""Trace the original party robot (ui/content/static/images/partyrobot.png) into the themed inline
SVG in src/bartendro/web/templates/_robot.html. Needs `pip install vtracer pillow`.

The PNG is flat colours: grey body, black lines, blue tray, orange drink / dots, white. It's scaled
up smoothly, every pixel snapped to the nearest of those colours, traced, and the colours that a
party should repaint are swapped for theme variables."""

import re
import tempfile
from pathlib import Path

import vtracer
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ui" / "content" / "static" / "images" / "partyrobot.png"
OUT = ROOT / "src" / "bartendro" / "web" / "templates" / "_robot.html"
SCALE = 3

# palette colour -> what the SVG uses
PALETTE = {
    (227, 228, 228): "var(--robot)",     # body, legs, arm, bubbles
    (0, 0, 0): "#000",                   # outlines, eyes, smile
    (157, 185, 222): "var(--robot-tray)",  # tray
    (243, 169, 108): "var(--robot-drink)", # the drink
    (238, 132, 33): "var(--robot-dots)",  # the dots
    (255, 255, 255): "#fff",             # glass rim, shine
}


NEUTRAL = [c for c in PALETTE if max(c) - min(c) < 10]


def snap(rgb):
    # a grey between the black lines and the grey body is closer to the tray blue than to either:
    # keep greys grey
    choices = NEUTRAL if max(rgb) - min(rgb) < 24 else PALETTE
    return min(choices, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, rgb, strict=True)))


def main():
    im = Image.open(SRC).convert("RGBA")
    # scale up smoothly, then snap each pixel to the palette: smooth edges instead of stair steps
    big = im.resize((im.width * SCALE, im.height * SCALE), Image.LANCZOS)
    out = Image.new("RGBA", big.size, (0, 0, 0, 0))
    src, dst = big.load(), out.load()
    cache = {}
    for y in range(big.height):
        for x in range(big.width):
            r, g, b, a = src[x, y]
            if a < 128:
                continue
            key = (r >> 2, g >> 2, b >> 2)
            if key not in cache:
                cache[key] = snap((r, g, b))
            dst[x, y] = (*cache[key], 255)
    with tempfile.TemporaryDirectory() as tmp:
        png, svg = Path(tmp) / "in.png", Path(tmp) / "out.svg"
        out.save(png)
        vtracer.convert_image_to_svg_py(str(png), str(svg), colormode="color", hierarchical="stacked",
                                        mode="spline", filter_speckle=8, color_precision=8,
                                        layer_difference=8, corner_threshold=60, length_threshold=8,
                                        splice_threshold=45, path_precision=1)
        text = svg.read_text()
    paths = []
    for m in re.finditer(r'<path d="([^"]+)" fill="#([0-9A-Fa-f]{6})" transform="translate\(([-\d.]+),([-\d.]+)\)"\s*/>',
                         text):
        d, hexcol, tx, ty = m.groups()
        rgb = tuple(int(hexcol[i:i + 2], 16) for i in (0, 2, 4))
        fill = PALETTE[snap(rgb)]
        t = f' transform="translate({tx} {ty})"' if (float(tx), float(ty)) != (0, 0) else ""
        paths.append(f'<path d="{d.strip()}" fill="{fill}"{t}/>')
    assert paths, "nothing traced"
    w, h = im.width * SCALE, im.height * SCALE
    OUT.write_text(
        "{# The Party Robotics robot: ui/content/static/images/partyrobot.png (GPL like this repo), traced\n"
        "   to SVG by scripts/trace_robot.py - don't edit by hand, re-run that. Inline so it follows the\n"
        "   theme: --robot (body), --robot-tray, --robot-drink, --robot-dots (static/style.css;\n"
        "   a party's colours set them in web/theme.py). #}\n"
        f'<svg class="robot-art" viewBox="0 0 {w} {h}" role="img" aria-label="Bartendro robot">\n'
        + "\n".join(paths) + "\n</svg>\n", encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(ROOT)}: {len(paths)} paths, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
