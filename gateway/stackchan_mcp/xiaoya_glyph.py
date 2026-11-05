# SPDX-License-Identifier: MIT
"""Local character cards for the round homework display."""

from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO
import math
import os
from pathlib import Path
from typing import Literal
import unicodedata

from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageDraw, ImageFont, ImageOps

MAX_PNG_BYTES = 24 * 1024
# Spec section 10, level 3: show the full character for 10 s (parent chose 10 s), then hide it so
# the child writes it herself.
DEFAULT_HOLD_MS = 10000
_FILES = {"kai": "FreeHKKai-4700.ttf", "sans": "NotoSansHK-Regular.otf"}


def vocab_mode(items, *, copy_mode_said: bool = False) -> Literal["dictation", "copy"]:
    if not isinstance(items, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("title"), str)
        or not item["title"].strip()
        or "done" not in item
        for item in items
    ):
        return "dictation"
    titles = [item["title"] for item in items if not item["done"]]
    if any("默" in title for title in titles):
        return "dictation"
    return "copy" if copy_mode_said and titles else "dictation"


def clean_characters(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Expected Chinese characters")
    chars = "".join(
        ch
        for ch in text
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )
    ranges = (
        (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF),
        (0xF900, 0xFAFF),
        (0x20000, 0x2A6DF),
        (0x2A700, 0x2B73F),
        (0x2B740, 0x2B81F),
        (0x2B820, 0x2CEAF),
        (0x2CEB0, 0x2EBEF),
        (0x30000, 0x3134F),
        (0x31350, 0x323AF),
        (0x2F800, 0x2FA1F),
    )
    if not 1 <= len(chars) <= 4 or any(
        ord(ch) != 0x3007 and not any(lo <= ord(ch) <= hi for lo, hi in ranges)
        for ch in chars
    ):
        raise ValueError("Expected 1–4 CJK ideographs")
    return chars


def _directory() -> str:
    return os.getenv("STACKCHAN_GLYPH_FONT_DIR", str(Path(__file__).with_name("fonts")))


@lru_cache(maxsize=8)
def _cmap(directory: str, name: str) -> frozenset[int]:
    with TTFont(Path(directory) / _FILES[name], lazy=True) as font:
        return frozenset(font.getBestCmap() or {})


def font_name_for(ch: str) -> Literal["kai", "sans"]:
    if len(ch) != 1:
        raise ValueError("Expected one character")
    try:
        for name in ("kai", "sans"):
            if ord(ch) in _cmap(_directory(), name):
                return name
    except (OSError, TTLibError) as exc:
        raise ValueError("Cannot load glyph fonts") from exc
    raise ValueError("Character absent from bundled fonts")


@lru_cache(maxsize=128)
def _font(directory: str, name: str, size: int):
    return ImageFont.truetype(str(Path(directory) / _FILES[name]), size)


def panel_mirrors_x() -> bool:
    """True when the device panel shows its frame buffer horizontally mirrored.

    The spotpear128cam firmware drives the GC9A01A with MADCTL 0x08 (no MX), which
    on the real board shows the frame buffer left-right mirrored (蝴蝶 read 蝶蝴 with
    the 虫 radical on the right). The 鈴鈴 face art is near-symmetric, so it looks
    right either way. We pre-mirror the glyph here so it reads correctly without a
    firmware change. Set STACKCHAN_GLYPH_MIRROR=0 if the firmware is ever fixed.
    """
    return os.getenv("STACKCHAN_GLYPH_MIRROR", "1").strip().lower() not in {"0", "false", "no", "off"}


def render_glyph_png(chars: str) -> bytes:
    return _render(clean_characters(chars), _directory(), panel_mirrors_x())


@lru_cache(maxsize=128)
def _render(chars: str, directory: str, mirror: bool = False) -> bytes:
    scale = 4
    count = len(chars)
    positions = {
        1: [(120, 120)],
        2: [(69, 120), (171, 120)],
        3: [(76, 77), (164, 77), (120, 163)],
        4: [(77, 77), (163, 77), (77, 163), (163, 163)],
    }[count]
    size = (136 if count == 1 else 96 if count == 2 else 82) * scale
    canvas = Image.new("L", (240 * scale, 240 * scale))
    draw = ImageDraw.Draw(canvas)
    try:
        for ch, (x, y) in zip(chars, positions):
            font = _font(directory, font_name_for(ch), size)
            left, top, right, bottom = draw.textbbox((0, 0), ch, font=font)
            draw.text(
                (x * scale - (left + right) / 2, y * scale - (top + bottom) / 2),
                ch,
                font=font,
                fill=255,
            )
    except OSError as exc:
        raise ValueError("Cannot render glyph") from exc
    # Fit the entire ink footprint, preserving the character shapes and layout.
    bbox = canvas.getbbox()
    if bbox is None:
        raise ValueError("Empty glyph")
    radius = max(
        math.hypot(x - 120 * scale, y - 120 * scale)
        for x, y in (
            (bbox[0], bbox[1]),
            (bbox[2], bbox[1]),
            (bbox[0], bbox[3]),
            (bbox[2], bbox[3]),
        )
    )
    if radius > 97 * scale:
        extent = int(canvas.width * 97 * scale / radius)
        smaller = canvas.resize((extent, extent), Image.Resampling.LANCZOS)
        canvas = Image.new("L", canvas.size)
        canvas.paste(smaller, ((canvas.width - extent) // 2,) * 2)
    canvas = canvas.resize((240, 240), Image.Resampling.LANCZOS)
    if mirror:
        # Pre-compensate the panel's horizontal mirror (see panel_mirrors_x).
        canvas = ImageOps.mirror(canvas)
    # Remove antialiasing filter tails outside the safe circle.
    pixels = canvas.load()
    for y in range(240):
        for x in range(240):
            if (x - 120) ** 2 + (y - 120) ** 2 > 100**2:
                pixels[x, y] = 0
    for levels in (16, 4):
        indexed = canvas.point(lambda v: round(v * (levels - 1) / 255)).convert("P")
        indexed.putpalette(
            [
                value
                for i in range(256)
                for value in [round(min(i, levels - 1) * 255 / (levels - 1))] * 3
            ]
        )
        output = BytesIO()
        indexed.save(output, format="PNG", optimize=True, bits=4)
        png = output.getvalue()
        if len(png) <= MAX_PNG_BYTES:
            return png
    raise ValueError("Glyph PNG exceeds device limit")


def glyph_message(png: bytes, hold_ms: int = DEFAULT_HOLD_MS) -> dict:
    if type(hold_ms) is not int or not 1000 <= hold_ms <= 60000:
        raise ValueError("Invalid hold duration")
    if len(png) > MAX_PNG_BYTES:
        raise ValueError("Glyph PNG exceeds device limit")
    return {
        "type": "xiaoya",
        "cmd": "glyph",
        "png": base64.b64encode(png).decode("ascii"),
        "hold_ms": hold_ms,
    }
