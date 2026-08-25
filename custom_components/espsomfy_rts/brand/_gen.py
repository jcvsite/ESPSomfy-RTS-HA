"""Rasterize the ESPSomfy controller logo into HA brand PNGs."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent
ACCENT = (0, 122, 255, 255)
WHITE = (255, 255, 255, 255)
# Master size; 48-unit logo from data-dev/favicon.svg
MASTER = 1024
U = MASTER / 48


def _draw_logo(size: int) -> Image.Image:
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = U

    d.rounded_rectangle((0, 0, MASTER - 1, MASTER - 1), radius=12 * s, fill=ACCENT)

    # Cassette
    d.rounded_rectangle((9 * s, 11 * s, 39 * s, 16.2 * s), radius=3 * s, fill=WHITE)
    # Slats
    d.rounded_rectangle((12 * s, 20 * s, 32 * s, 23.4 * s), radius=1.7 * s, fill=WHITE)
    d.rounded_rectangle((12 * s, 26.5 * s, 32 * s, 29.9 * s), radius=1.7 * s, fill=WHITE)
    d.rounded_rectangle((12 * s, 33 * s, 26 * s, 36.4 * s), radius=1.7 * s, fill=WHITE)

    stroke = max(2, round(2.2 * s))
    # Radio arcs (right semicircles), matching favicon.svg
    cy = 30.25 * s
    for cx, r in ((36.5 * s, 6.2 * s), (40.2 * s, 10.2 * s)):
        box = (cx - r, cy - r, cx + r, cy + r)
        d.arc(box, start=270, end=90, fill=WHITE, width=stroke)

    if size == MASTER:
        return img
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    icon = _draw_logo(256)
    icon2x = _draw_logo(512)
    for name in (
        "icon.png",
        "logo.png",
        "dark_icon.png",
        "dark_logo.png",
    ):
        icon.save(OUT / name, "PNG")
        print(name)
    for name in (
        "icon@2x.png",
        "logo@2x.png",
        "dark_icon@2x.png",
        "dark_logo@2x.png",
    ):
        icon2x.save(OUT / name, "PNG")
        print(name)


if __name__ == "__main__":
    main()
