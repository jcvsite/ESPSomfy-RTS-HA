"""Generate open/closed/partial window SVGs matching the ESPSomfy web UI."""

from pathlib import Path

OUT = Path(__file__).parent

ACCENT = "#007AFF"
ACCENT_DARK = "#0051A8"
GLASS = "#D6E4F7"
FRAME = "#D1D1D6"
HI = "#F5F5F7"
POST = "#8E8E93"

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n{body}\n</svg>\n'


def _svg(body: str) -> str:
    return SVG.format(body=body)


def _cassette() -> str:
    return (
        f'<rect x="6" y="6" width="52" height="10" rx="3.2" fill="{FRAME}"/>'
        f'<path d="M12 10h40" stroke="{HI}" stroke-width="1.5" stroke-linecap="round"/>'
    )


def _sill() -> str:
    return f'<rect x="6" y="50" width="52" height="8" rx="3.2" fill="{FRAME}"/>'


def _glass() -> str:
    return (
        f'<rect x="11" y="16" width="42" height="34" rx="2.5" fill="{GLASS}"/>'
        '<path d="M32 17.5v31" stroke="#fff" stroke-opacity=".3" stroke-width="1.2"/>'
    )


def _window(inner: str) -> str:
    return _svg(f"{_glass()}{inner}{_cassette()}{_sill()}")


def _weave(height: float) -> str:
    lines = []
    y = 19.4
    while y < 16 + height - 1.2:
        lines.append(
            f'<path d="M11 {y:.1f}h42" stroke="#000" stroke-opacity=".14" stroke-width="1.15"/>'
        )
        y += 3.5
    return (
        f'<rect x="11" y="16" width="42" height="{height:.1f}" rx="1.4" fill="{ACCENT}"/>'
        + "".join(lines)
    )


def _slats(count: int, thick: float = 2.5, step: float = 3.6) -> str:
    parts = [
        f'<rect x="13.4" y="16" width="1.8" height="34" rx="0.9" fill="{POST}"/>',
        f'<rect x="48.8" y="16" width="1.8" height="34" rx="0.9" fill="{POST}"/>',
    ]
    y = 17.2
    for _ in range(count):
        parts.append(
            f'<rect x="11" y="{y:.1f}" width="42" height="{thick}" rx="{thick / 2:.2f}" fill="{ACCENT}"/>'
        )
        y += step
    return "".join(parts)


def _shutter(count: int) -> str:
    fill_h = min(34.0, 2.2 + count * 3.6)
    parts = [
        f'<rect x="11" y="16" width="42" height="{fill_h:.1f}" rx="1.2" fill="{ACCENT_DARK}"/>'
    ]
    y = 17.4
    for _ in range(count):
        parts.append(
            f'<rect x="12" y="{y:.1f}" width="40" height="2.7" rx="0.85" fill="{ACCENT}"/>'
        )
        y += 3.6
    return "".join(parts)


def _folds(start_x: float, count: int, *, right: bool = False) -> str:
    w, gap = 3.4, 0.85
    parts = []
    for i in range(count):
        x = (start_x - w - i * (w + gap)) if right else (start_x + i * (w + gap))
        parts.append(
            f'<rect x="{x:.1f}" y="16" width="{w}" height="34" rx="1.6" fill="{ACCENT}"/>'
        )
    return "".join(parts)


def _curtain(left: int, right: int) -> str:
    return (
        f'<rect x="11" y="16" width="42" height="34" fill="{ACCENT_DARK}" opacity=".28"/>'
        + _folds(11.6, left)
        + _folds(52.4, right, right=True)
    )


def _awning(height: float) -> str:
    if height <= 0:
        return _svg(_cassette())
    stripes = []
    x = 18.5
    while x < 50:
        stripes.append(
            f'<rect x="{x:.1f}" y="16" width="2.4" height="{height:.1f}" fill="#000" fill-opacity=".18"/>'
        )
        x += 8.2
    return _svg(
        f'<rect x="11" y="16" width="42" height="{height:.1f}" rx="1.6" fill="{ACCENT}"/>'
        + "".join(stripes)
        + f'<rect x="11" y="{16 + height - 2.2:.1f}" width="42" height="2.2" rx="1.1" fill="{FRAME}"/>'
        + _cassette()
    )


def _garage(panels: int) -> str:
    roof = (
        f'<path d="M32 5 L58 18.5 v2.4 H6 v-2.4 Z" fill="{FRAME}"/>'
        f'<path d="M32 5 L58 18.5 v1.3 L32 6.4 6 19.8 v-1.3 Z" fill="{HI}"/>'
    )
    opening = f'<rect x="12" y="21" width="40" height="33" rx="1.6" fill="{GLASS}"/>'
    door = []
    if panels:
        door.append(
            f'<rect x="12" y="21" width="40" height="{min(33, panels * 8.2):.1f}" rx="1.4" fill="{ACCENT_DARK}"/>'
        )
        y = 22.2
        for _ in range(panels):
            door.append(
                f'<rect x="13.4" y="{y:.1f}" width="37.2" height="6.4" rx="1.1" fill="{ACCENT}"/>'
            )
            y += 8.0
        if panels >= 3:
            door.append(
                f'<rect x="30.2" y="{y - 4.6:.1f}" width="3.6" height="1.6" rx="0.8" fill="{ACCENT_DARK}"/>'
            )
    sill = f'<rect x="6" y="54" width="52" height="5" rx="2" fill="{FRAME}"/>'
    return _svg(opening + "".join(door) + roof + sill)


def _bars(xs: list[float]) -> str:
    parts = []
    for x in xs:
        parts.append(
            f'<rect x="{x:.1f}" y="14" width="2.4" height="38" rx="1.2" fill="{POST}"/>'
            f'<circle cx="{x + 1.2:.1f}" cy="14" r="1.55" fill="{FRAME}"/>'
        )
    return "".join(parts)


def _gate(xs: list[float]) -> str:
    posts = (
        f'<rect x="6" y="8" width="6" height="48" rx="2.2" fill="{POST}"/>'
        f'<rect x="52" y="8" width="6" height="48" rx="2.2" fill="{POST}"/>'
    )
    veil = ""
    if xs:
        clusters: list[list[float]] = [[xs[0]]]
        for x in xs[1:]:
            if x - clusters[-1][-1] > 6:
                clusters.append([x])
            else:
                clusters[-1].append(x)
        for cluster in clusters:
            x0, x1 = cluster[0], cluster[-1] + 2.4
            veil += (
                f'<rect x="{x0:.1f}" y="14" width="{x1 - x0:.1f}" height="38" rx="1.4" fill="{POST}" opacity=".28"/>'
            )
    return _svg(veil + _bars(xs) + posts)


VARIANTS = {
    "shade": {
        "open": _window(_weave(7)),
        "partial": _window(_weave(18)),
        "closed": _window(_weave(34)),
    },
    "blind": {
        "open": _window(_slats(2)),
        "partial": _window(_slats(5)),
        "closed": _window(_slats(9)),
    },
    "curtain": {
        "open": _window(_curtain(2, 2)),
        "partial": _window(_curtain(4, 4)),
        "closed": _window(_curtain(6, 6)),
    },
    "awning": {
        "open": _awning(36),
        "partial": _awning(18),
        "closed": _awning(0),
    },
    "shutter": {
        "open": _window(_shutter(2)),
        "partial": _window(_shutter(5)),
        "closed": _window(_shutter(9)),
    },
    "garage": {
        "open": _garage(1),
        "partial": _garage(2),
        "closed": _garage(4),
    },
    "gate": {
        "open": _gate([]),
        "partial": _gate([14.5, 18.5, 41.1, 45.1]),
        "closed": _gate([14.5, 18.5, 22.5, 26.5, 30.5, 34.5, 38.5, 42.5, 46.5]),
    },
}


def main() -> None:
    for stem, states in VARIANTS.items():
        for state, xml in states.items():
            path = OUT / f"{stem}_{state}.svg"
            path.write_text(xml, encoding="utf-8")
            print(path.name)


if __name__ == "__main__":
    main()
