"""Generate lcars_tui/assets/lcars.ico -- an LCARS-panel-inspired app icon.

Pure stdlib (struct + zlib) PNG/ICO writer -- Pillow isn't available for the
project's Python version in the offline wheelhouse, so icon pixels are drawn
and encoded by hand instead. Re-run this script (``python tools/make_icon.py``)
any time the design needs to change; the .ico is a generated, committed asset
consumed by ``lcars.spec`` at build time (PyInstaller's ``icon=`` argument
just needs the file to exist on disk, so it's checked in rather than
regenerated as part of the build).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ORANGE = (255, 153, 0)
LILAC = (153, 153, 204)
GOLD = (255, 204, 153)
BLACK = (0, 0, 0)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "lcars_tui" / "assets"
OUT_PATH = ASSETS_DIR / "lcars.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _dist(x0: float, y0: float, x1: float, y1: float) -> float:
    return ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5


def draw_lcars(n: int) -> list[list[tuple[int, int, int, int]]]:
    """Return an n x n RGBA pixel grid depicting a simple LCARS elbow panel."""
    left_w = max(2, round(n * 0.34))
    top_h = max(2, round(n * 0.30))
    fillet = max(1, round(min(left_w, top_h) * 0.7))
    split_y = round(n * 0.62)  # where the vertical bar changes from orange to lilac
    gap = max(1, round(n * 0.02))  # thin black separator between color segments
    gold_size = max(2, round(n * 0.20))
    corner_r = max(1, round(min(left_w, top_h) * 0.6))

    px: list[list[tuple[int, int, int, int]]] = [
        [BLACK + (255,) for _ in range(n)] for _ in range(n)
    ]

    def set_px(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < n and 0 <= y < n:
            px[y][x] = color + (255,)

    for y in range(n):
        for x in range(n):
            in_top_bar = y < top_h
            in_left_bar = x < left_w
            if not (in_top_bar or in_left_bar):
                continue

            # Concave fillet where the two bars meet on the inside.
            if left_w <= x < left_w + fillet and top_h <= y < top_h + fillet:
                if _dist(x, y, left_w + fillet, top_h + fillet) > fillet:
                    pass  # keep colored (falls through below)
                else:
                    continue  # stays black -- inside the rounded notch

            # Rounded outer end-cap: top-right corner of the horizontal bar.
            if in_top_bar and x >= n - corner_r and y < corner_r:
                if _dist(x, y, n - corner_r, corner_r) > corner_r:
                    continue

            # Rounded outer end-cap: bottom-left corner of the vertical bar.
            if in_left_bar and y >= n - corner_r and x < corner_r:
                if _dist(x, y, corner_r, n - corner_r) > corner_r:
                    continue

            # Thin black gap separating the two-tone vertical bar segments.
            if in_left_bar and not in_top_bar and split_y - gap <= y < split_y + gap:
                continue

            if in_left_bar and not in_top_bar and y >= split_y + gap:
                set_px(x, y, LILAC)
            else:
                set_px(x, y, ORANGE)

    # Small gold accent block, bottom-right -- a classic LCARS "button".
    margin = max(1, round(n * 0.06))
    for y in range(n - margin - gold_size, n - margin):
        for x in range(n - margin - gold_size, n - margin):
            set_px(x, y, GOLD)

    return px


def _png_bytes(px: list[list[tuple[int, int, int, int]]]) -> bytes:
    n = len(px)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for row in px:
        raw.append(0)  # filter type: none
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    ihdr = struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def build_ico(sizes: tuple[int, ...]) -> bytes:
    images = [_png_bytes(draw_lcars(n)) for n in sizes]

    header = struct.pack("<HHH", 0, 1, len(sizes))
    dir_entries = bytearray()
    data_blob = bytearray()
    offset = 6 + 16 * len(sizes)
    for n, img in zip(sizes, images):
        wh = 0 if n >= 256 else n
        dir_entries += struct.pack(
            "<BBBBHHII", wh, wh, 0, 0, 1, 32, len(img), offset
        )
        data_blob += img
        offset += len(img)

    return header + bytes(dir_entries) + bytes(data_blob)


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(build_ico(SIZES))
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
