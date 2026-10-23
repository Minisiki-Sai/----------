"""Render the "DN 倒计时" application icon.

Pure standard library: the icon is drawn analytically (signed distance fields)
so every size is crisp, then written as PNG and as a multi-size Windows .ico
(PNG for 256 px, uncompressed 32-bit DIBs for the small sizes, which is what
Windows shell and the system tray load).

Usage: python tools/make_app_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNG_TARGETS = [
    ROOT / "frontend" / "public" / "app-icon.png",
    ROOT / "dragon_nest_builder" / "assets" / "app_icon.png",
]
ICO_TARGETS = [
    ROOT / "dragon_nest_builder" / "assets" / "app_icon_build.ico",
    ROOT / "dragon_nest_builder" / "assets" / "app_icon.ico",
]

# Palette: low-saturation blue-grey glass with one warm accent (the Boss skill
# overlay).  Values are straight sRGB.
BG_TOP = (0x9C, 0xBD, 0xD9)
BG_BOTTOM = (0x5A, 0x82, 0xA9)
RING = (0xFF, 0xFF, 0xFF)
CARD = (0xF2, 0xF7, 0xFC)
ACCENT = (0xE9, 0xA2, 0x6B)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def _mix(a, b, t: float):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _rounded_rect_sdf(x: float, y: float, half_w: float, half_h: float, radius: float) -> float:
    dx = abs(x) - half_w + radius
    dy = abs(y) - half_h + radius
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def _ring_sdf(x: float, y: float, radius: float, thickness: float) -> float:
    return abs(math.hypot(x, y) - radius) - thickness


def _arc_mask(x: float, y: float, gap_center: float, gap_half: float) -> bool:
    """True inside the arc (i.e. outside the dial's gap)."""
    angle = math.atan2(y, x)
    delta = (angle - gap_center + math.pi) % (2.0 * math.pi) - math.pi
    return abs(delta) > gap_half


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    """Return `size` rows of RGBA tuples, anti-aliased."""
    rows: list[list[tuple[int, int, int, int]]] = []
    # Geometric ratios relative to a 1024 px design grid.
    scale = size / 1024.0
    pad = 34.0 * scale
    half = size / 2.0 - pad
    corner = 232.0 * scale
    ring_radius = 296.0 * scale
    ring_thickness = 62.0 * scale
    inner_half_w = 168.0 * scale
    inner_half_h = 44.0 * scale
    inner_corner = inner_half_h
    bar_half_w = 118.0 * scale
    bar_half_h = 26.0 * scale
    bar_center_y = 108.0 * scale
    edge = max(1.0, 1.0)  # 1 device pixel of anti-aliasing ramp

    for py in range(size):
        y = py + 0.5 - size / 2.0
        row: list[tuple[int, int, int, int]] = []
        for px in range(size):
            x = px + 0.5 - size / 2.0

            # Background squircle.
            body = _clamp(0.5 - _rounded_rect_sdf(x, y, half, half, corner) / edge)
            if body <= 0.0:
                row.append((0, 0, 0, 0))
                continue

            t = _clamp((y + half) / (2.0 * half))
            color = _mix(BG_TOP, BG_BOTTOM, t * t * (3 - 2 * t))

            # Soft top highlight for the glass feel.
            highlight = _clamp(1.0 - (t * 2.6)) * 0.20
            color = _mix(color, (255, 255, 255), highlight)

            alpha = body

            # Clock ring with a dial gap at the upper right.
            ring = _clamp(0.5 - _ring_sdf(x, y, ring_radius, ring_thickness) / edge)
            if ring > 0.0 and _arc_mask(x, y, gap_center=-math.pi / 2, gap_half=0.20):
                color = _mix(color, RING, ring * 0.96)

            # Glass capsule inside the ring: the "countdown bar".
            card = _clamp(0.5 - _rounded_rect_sdf(x, y, inner_half_w, inner_half_h, inner_corner) / edge)
            if card > 0.0:
                # Track plus an elapsed portion, so the bar reads as a countdown.
                elapsed = _clamp(0.5 - _rounded_rect_sdf(
                    x + inner_half_w * 0.62, y, inner_half_w * 0.38, inner_half_h, inner_corner) / edge)
                color = _mix(color, CARD, card * 0.92)
                color = _mix(color, (0x4C, 0x76, 0xA3), elapsed * 0.9)

            # Warm accent bar (Boss skill countdown).
            bar = _clamp(0.5 - _rounded_rect_sdf(x, y - bar_center_y, bar_half_w, bar_half_h, bar_half_h) / edge)
            if bar > 0.0:
                color = _mix(color, ACCENT, bar * 0.95)

            row.append((int(_clamp(color[0], 0, 255)), int(_clamp(color[1], 0, 255)),
                        int(_clamp(color[2], 0, 255)), int(round(alpha * 255))))
        rows.append(row)
    return rows


def _png_bytes(rows: list[list[tuple[int, int, int, int]]]) -> bytes:
    height = len(rows)
    width = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for r, g, b, a in row:
            raw.extend((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _dib_bytes(rows: list[list[tuple[int, int, int, int]]]) -> bytes:
    height = len(rows)
    width = len(rows[0])
    pixels = bytearray()
    for row in reversed(rows):
        for r, g, b, a in row:
            pixels.extend((b, g, r, a))
    mask_row = ((width + 31) // 32) * 4
    mask = bytes(mask_row * height)
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0,
                         len(pixels) + len(mask), 0, 0, 0, 0)
    return header + bytes(pixels) + mask


def write_ico(path: Path, sizes: list[int]) -> None:
    images: list[tuple[int, bytes]] = []
    for size in sizes:
        rows = render(size)
        images.append((size, _png_bytes(rows) if size >= 256 else _dib_bytes(rows)))
    count = len(images)
    offset = 6 + 16 * count
    directory = bytearray(struct.pack("<HHH", 0, 1, count))
    payload = bytearray()
    for size, data in images:
        directory.extend(struct.pack("<BBBBHHII", size if size < 256 else 0,
                                     size if size < 256 else 0, 0, 0, 1, 32,
                                     len(data), offset + len(payload)))
        payload.extend(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(directory) + bytes(payload))


def main() -> None:
    for target in PNG_TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_png_bytes(render(512 if "public" in target.parts else 1024)))
        print("png ", target.relative_to(ROOT))
    for target in ICO_TARGETS:
        write_ico(target, [256, 128, 64, 48, 32, 16])
        print("ico ", target.relative_to(ROOT))


if __name__ == "__main__":
    main()
