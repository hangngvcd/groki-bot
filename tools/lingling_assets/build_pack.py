#!/usr/bin/env python3
# SPDX-License-Identifier: BSL-1.0
"""Build a 鈴鈴 "LLAP" v1 asset pack for the ``model`` flash partition.

Flash the result at 0xdb1000 (partition ``model`` + 0x1000). Format,
little-endian (must match components/lingling_assets/assets.cpp):

  header 16 B : "LLAP", u16 version=1, u16 count, u32 pack_size, u32 reserved=0
  entry  52 B : char name[32] (NUL padded), u32 offset, u32 size,
                u16 kind (1=PNG, 2=PCM s16le mono), u16 reserved=0,
                u32 sample_rate (0 for PNG), u32 crc32 (zlib/IEEE)
  blobs       : 4-byte aligned

Input files:
  images: ``f<1-4>_<expr>.png`` -> ``face.f<N>.<expr>``; ``<expr>.png`` -> ``face.<expr>``
  clips : ``<name>.wav`` (mono s16le) or ``<name>.pcm`` + --pcm-rate; name e.g. ``vo.intro.1``

    python3 tools/lingling_assets/build_pack.py OUT.bin [--images DIR] [--clips DIR]
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import wave
import zlib
from pathlib import Path

MAGIC = b"LLAP"
VERSION = 1
HEADER = struct.Struct("<4sHHII")
ENTRY = struct.Struct("<32sIIHHII")
KIND_PNG = 1
KIND_PCM = 2
PACK_OFFSET = 0xDB1000
MAX_SIZE = 0x240000 - 0x1000  # model partition minus the esp-sr sector
PNG_SIG = b"\x89PNG\r\n\x1a\n"

EXPRESSIONS = (
    "neutral happy sad angry doubt sleepy listening thinking excited curious "
    "confused surprised dizzy affection bored"
).split()
CLIP_NAMES = ("vo.intro.1", "vo.evolve.f2", "vo.evolve.f3", "vo.evolve.f4")
_FORM_IMG = re.compile(r"^f([1-4])_([a-z]+)\.png$")
_BARE_IMG = re.compile(r"^([a-z]+)\.png$")


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != PNG_SIG or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def collect_images(folder: Path) -> list[tuple[str, int, int, bytes]]:
    out = []
    for f in sorted(folder.glob("*.png")):
        m = _FORM_IMG.match(f.name)
        if m:
            name, expr = f"face.f{m.group(1)}.{m.group(2)}", m.group(2)
        else:
            m = _BARE_IMG.match(f.name)
            if not m:
                raise SystemExit(f"{f.name}: expected f<1-4>_<expr>.png or <expr>.png")
            name, expr = f"face.{m.group(1)}", m.group(1)
        if expr not in EXPRESSIONS:
            raise SystemExit(f"{f.name}: unknown expression {expr!r} (one of {', '.join(EXPRESSIONS)})")
        data = f.read_bytes()
        w, h = png_size(data)
        if (w, h) != (240, 240):
            raise SystemExit(f"{f.name}: must be 240x240, got {w}x{h}")
        out.append((name, KIND_PNG, 0, data))
    return out


def collect_clips(folder: Path, pcm_rate: int) -> list[tuple[str, int, int, bytes]]:
    out = []
    for f in sorted(folder.iterdir()):
        if f.suffix == ".wav":
            with wave.open(str(f), "rb") as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise SystemExit(f"{f.name}: must be mono 16-bit")
                rate, data = w.getframerate(), w.readframes(w.getnframes())
        elif f.suffix == ".pcm":
            rate, data = pcm_rate, f.read_bytes()
        else:
            continue
        name = f.stem
        if name not in CLIP_NAMES:
            print(f"warning: {f.name}: clip name not used by firmware ({', '.join(CLIP_NAMES)})", file=sys.stderr)
        if not 8000 <= rate <= 192000 or len(data) % 2 or not data:
            raise SystemExit(f"{f.name}: bad rate/length")
        out.append((name, KIND_PCM, rate, data))
    return out


def build(items: list[tuple[str, int, int, bytes]]) -> bytes:
    names = [i[0] for i in items]
    if len(set(names)) != len(names):
        raise SystemExit("duplicate asset names")
    table_end = HEADER.size + ENTRY.size * len(items)
    offset = (table_end + 3) & ~3
    entries, blobs = [], []
    for name, kind, rate, data in items:
        raw = name.encode("ascii")
        if not 0 < len(raw) < 32:
            raise SystemExit(f"{name}: name must be 1..31 ASCII bytes")
        entries.append(ENTRY.pack(raw, offset, len(data), kind, 0, rate, zlib.crc32(data) & 0xFFFFFFFF))
        pad = (-len(data)) % 4
        blobs.append(data + b"\0" * pad)
        offset += len(data) + pad
    body = b"".join(entries)
    body += b"\0" * (((table_end + 3) & ~3) - table_end)
    body += b"".join(blobs)
    total = HEADER.size + len(body)
    if total > MAX_SIZE:
        raise SystemExit(f"pack is {total} bytes, max {MAX_SIZE}")
    return HEADER.pack(MAGIC, VERSION, len(items), total, 0) + body


def parse(pack: bytes) -> list[dict]:
    """Reference parser (mirrors the firmware) for tests and `--list`."""
    magic, ver, count, size, res = HEADER.unpack_from(pack, 0)
    assert magic == MAGIC and ver == VERSION and res == 0 and size <= len(pack)
    out = []
    for i in range(count):
        raw, off, n, kind, r2, rate, crc = ENTRY.unpack_from(pack, HEADER.size + i * ENTRY.size)
        data = pack[off : off + n]
        assert off % 4 == 0 and off + n <= size and r2 == 0 and zlib.crc32(data) & 0xFFFFFFFF == crc
        out.append({"name": raw.rstrip(b"\0").decode(), "kind": kind, "size": n, "rate": rate})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--images", type=Path)
    ap.add_argument("--clips", type=Path)
    ap.add_argument("--pcm-rate", type=int, default=16000)
    a = ap.parse_args()
    items = []
    if a.images:
        items += collect_images(a.images)
    if a.clips:
        items += collect_clips(a.clips, a.pcm_rate)
    if not items:
        raise SystemExit("nothing to pack (give --images and/or --clips)")
    pack = build(items)
    a.out.write_bytes(pack)
    print(f"{a.out}: {len(items)} entries, {len(pack)} bytes (max {MAX_SIZE}); flash at 0x{PACK_OFFSET:x}")
    for e in parse(pack):
        print(f"  {e['name']:<24} kind={e['kind']} size={e['size']} rate={e['rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
