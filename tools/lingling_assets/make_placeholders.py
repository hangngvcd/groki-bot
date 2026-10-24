#!/usr/bin/env python3
# SPDX-License-Identifier: BSL-1.0
"""Programmatic placeholder art for 鈴鈴 (white cat) growth forms F1..F4.

Writes 240x240 PNGs named ``f<form>_<expr>.png`` (see build_pack.py for the
slot names). Placeholder only: swap any file for real art of the same name.

    python3 tools/lingling_assets/make_placeholders.py OUT_DIR [--expr neutral happy ...]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw

EXPRESSIONS = (
    "neutral happy sad angry doubt sleepy listening thinking excited curious "
    "confused surprised dizzy affection bored"
).split()

S = 4  # supersampling
W = 240
C = W // 2

WHITE = (250, 250, 252)
OUTLINE = (200, 205, 215)
PINK = (255, 150, 180)
BLUSH = (255, 170, 195)
EYE = (40, 44, 60)
LEAF = (120, 200, 90)
STEM = (90, 160, 70)
WING = (215, 235, 255)
WING_EDGE = (150, 190, 235)
GEM = (60, 210, 220)
GEM_HI = (190, 250, 255)
GOLD = (255, 200, 60)
GOLD_DARK = (220, 150, 30)
STAR = (255, 240, 120)


def p(x: float, y: float) -> tuple[float, float]:
    return (x * S, y * S)


def ellipse(d, cx, cy, rx, ry, fill, outline=None, width=0):
    d.ellipse([p(cx - rx, cy - ry), p(cx + rx, cy + ry)], fill=fill, outline=outline, width=width * S)


def poly(d, pts, fill, outline=None, width=0):
    d.polygon([p(x, y) for x, y in pts], fill=fill, outline=outline, width=width * S if outline else 0)


def line(d, pts, fill, width):
    d.line([p(x, y) for x, y in pts], fill=fill, width=int(width * S), joint="curve")


def arc(d, cx, cy, rx, ry, start, end, fill, width):
    d.arc([p(cx - rx, cy - ry), p(cx + rx, cy + ry)], start, end, fill=fill, width=int(width * S))


HEAD_CX, HEAD_CY, HEAD_R = C, 132, 60


def wings(d, size: float):
    for side in (-1, 1):
        base_x = HEAD_CX + side * (HEAD_R - 8)
        tip_x = HEAD_CX + side * (HEAD_R + 30 * size)
        pts = [
            (base_x, HEAD_CY - 10),
            (tip_x, HEAD_CY - 40 * size),
            (tip_x + side * 4, HEAD_CY - 10 * size),
            (tip_x - side * 6 * size, HEAD_CY + 12 * size),
            (base_x, HEAD_CY + 20),
        ]
        poly(d, pts, WING, WING_EDGE, 2)
        for k in range(1, 3):
            fx = base_x + side * (tip_x - base_x) * k / 3 * side * side
            line(d, [(base_x, HEAD_CY + 4), (fx, HEAD_CY - 30 * size * k / 3)], WING_EDGE, 2)


def ears(d):
    for side in (-1, 1):
        outer = [
            (HEAD_CX + side * 52, HEAD_CY - 30),
            (HEAD_CX + side * 46, HEAD_CY - 82),
            (HEAD_CX + side * 14, HEAD_CY - 56),
        ]
        poly(d, outer, WHITE, OUTLINE, 2)
        inner = [
            (HEAD_CX + side * 44, HEAD_CY - 40),
            (HEAD_CX + side * 42, HEAD_CY - 70),
            (HEAD_CX + side * 22, HEAD_CY - 54),
        ]
        poly(d, inner, PINK)


def sprout(d):
    top = HEAD_CY - HEAD_R
    line(d, [(HEAD_CX, top + 4), (HEAD_CX + 2, top - 18)], STEM, 4)
    poly(d, [(HEAD_CX + 2, top - 16), (HEAD_CX - 18, top - 30), (HEAD_CX - 6, top - 12)], LEAF)
    poly(d, [(HEAD_CX + 2, top - 16), (HEAD_CX + 22, top - 30), (HEAD_CX + 10, top - 12)], LEAF)


def gem(d):
    y = HEAD_CY - HEAD_R - 2
    poly(d, [(HEAD_CX, y - 20), (HEAD_CX + 13, y - 6), (HEAD_CX, y + 10), (HEAD_CX - 13, y - 6)], GEM, (30, 150, 170), 2)
    poly(d, [(HEAD_CX - 3, y - 13), (HEAD_CX + 4, y - 7), (HEAD_CX - 2, y - 2)], GEM_HI)


def star(d, cx, cy, r, fill):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    poly(d, pts, fill)


def crown(d):
    y = HEAD_CY - HEAD_R + 6
    pts = [
        (HEAD_CX - 26, y), (HEAD_CX - 30, y - 24), (HEAD_CX - 14, y - 12),
        (HEAD_CX, y - 30), (HEAD_CX + 14, y - 12), (HEAD_CX + 30, y - 24), (HEAD_CX + 26, y),
    ]
    poly(d, pts, GOLD, GOLD_DARK, 2)
    star(d, HEAD_CX, y - 40, 11, STAR)
    for x in (-14, 0, 14):
        ellipse(d, HEAD_CX + x, y - 6, 3, 3, (255, 90, 120))
    # small orbiting planet dot
    ellipse(d, HEAD_CX + 48, y - 34, 5, 5, (170, 140, 255))


def eyes(d, expr: str):
    ly, lx, rx = HEAD_CY - 6, HEAD_CX - 24, HEAD_CX + 24
    for x in (lx, rx):
        if expr in ("happy", "excited", "affection"):
            arc(d, x, ly + 4, 11, 9, 200, 340, EYE, 4)
        elif expr in ("sleepy", "bored"):
            line(d, [(x - 10, ly + 2), (x + 10, ly + 2)], EYE, 4)
        elif expr == "sad":
            ellipse(d, x, ly + 2, 8, 9, EYE)
            line(d, [(x - 12, ly - 14 + (4 if x < C else 0)), (x + 12, ly - 14 + (0 if x < C else 4))], EYE, 3)
        elif expr == "angry":
            ellipse(d, x, ly + 2, 8, 8, EYE)
            line(d, [(x - 12, ly - 16 + (0 if x < C else 6)), (x + 12, ly - 16 + (6 if x < C else 0))], EYE, 3)
        elif expr == "surprised":
            ellipse(d, x, ly, 11, 13, EYE)
            ellipse(d, x - 3, ly - 5, 4, 4, WHITE)
        elif expr == "dizzy":
            line(d, [(x - 8, ly - 8), (x + 8, ly + 8)], EYE, 4)
            line(d, [(x - 8, ly + 8), (x + 8, ly - 8)], EYE, 4)
        elif expr in ("thinking", "doubt"):
            ellipse(d, x + 4, ly - 4, 8, 10, EYE)
            ellipse(d, x + 2, ly - 8, 3, 3, WHITE)
        elif expr in ("listening", "curious"):
            ellipse(d, x, ly, 10, 12, EYE)
            ellipse(d, x - 3, ly - 5, 4, 4, WHITE)
        elif expr == "confused":
            r = 9 if x < C else 6
            ellipse(d, x, ly, r, r + 1, EYE)
        else:
            ellipse(d, x, ly, 9, 11, EYE)
            ellipse(d, x - 3, ly - 4, 3, 3, WHITE)


def mouth(d, expr: str):
    ny = HEAD_CY + 14
    poly(d, [(HEAD_CX - 6, ny - 3), (HEAD_CX + 6, ny - 3), (HEAD_CX, ny + 3)], PINK)
    if expr in ("surprised", "excited"):
        ellipse(d, HEAD_CX, ny + 14, 7, 8, (200, 80, 100))
    elif expr in ("sad", "bored"):
        arc(d, HEAD_CX, ny + 16, 9, 6, 200, 340, EYE, 3)
    else:
        arc(d, HEAD_CX - 7, ny + 4, 7, 6, 10, 170, EYE, 3)
        arc(d, HEAD_CX + 7, ny + 4, 7, 6, 10, 170, EYE, 3)
    for side in (-1, 1):
        for k, dy in enumerate((-4, 4)):
            line(d, [(HEAD_CX + side * 30, ny + dy), (HEAD_CX + side * 56, ny + dy * 2 - 2)], OUTLINE, 2)
    if expr in ("affection", "happy"):
        ellipse(d, HEAD_CX - 38, ny + 2, 9, 5, BLUSH)
        ellipse(d, HEAD_CX + 38, ny + 2, 9, 5, BLUSH)


def extras(d, expr: str):
    if expr == "sleepy":
        for i, (x, y, s) in enumerate([(170, 70, 8), (184, 54, 10), (198, 36, 12)]):
            line(d, [(x, y), (x + s, y), (x, y + s), (x + s, y + s)], WING_EDGE, 2)
    if expr in ("thinking", "confused", "doubt"):
        for i, r in enumerate((3, 5, 7)):
            ellipse(d, 172 + i * 10, 96 - i * 12, r, r, WING_EDGE)
    if expr == "affection":
        cx, cy = 182, 70
        ellipse(d, cx - 5, cy, 6, 6, PINK)
        ellipse(d, cx + 5, cy, 6, 6, PINK)
        poly(d, [(cx - 11, cy + 2), (cx + 11, cy + 2), (cx, cy + 14)], PINK)


def render(form: int, expr: str) -> Image.Image:
    img = Image.new("RGBA", (W * S, W * S), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    if form >= 2:
        wings(d, {2: 0.6, 3: 0.9, 4: 1.2}[form])
    ears(d)
    ellipse(d, HEAD_CX, HEAD_CY, HEAD_R, HEAD_R - 4, WHITE, OUTLINE, 2)
    if form <= 2:
        sprout(d)
    elif form == 3:
        gem(d)
    else:
        crown(d)
    eyes(d, expr)
    mouth(d, expr)
    extras(d, expr)
    img = img.resize((W, W), Image.LANCZOS)
    # Keep the round panel safe zone: black outside r=118.
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).ellipse([C - 118, C - 118, C + 118, C + 118], fill=255)
    out = Image.new("RGB", (W, W), (0, 0, 0))
    out.paste(img.convert("RGB"), (0, 0), mask)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--expr", nargs="*", default=list(EXPRESSIONS))
    ap.add_argument("--forms", nargs="*", type=int, default=[1, 2, 3, 4])
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for form in a.forms:
        for expr in a.expr:
            if expr not in EXPRESSIONS:
                raise SystemExit(f"unknown expression {expr}")
            render(form, expr).save(a.out / f"f{form}_{expr}.png", optimize=True)
    print(f"wrote {len(a.forms) * len(a.expr)} images to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
