#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Rebuild stackchan_mcp/data/char_structure.json.gz from a cjkvi ids.txt.

Usage:
  python3 tools/build_char_structure.py /path/to/ids.txt
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

# Keep radical nicknames in sync with the generator used for ticket 36.
# Importing from the package keeps runtime and rebuild aligned for structures.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stackchan_mcp.char_structure import IDS_STRUCTURE, _FALLBACK_RADICALS  # noqa: E402

# Full nickname table is embedded in the shipped gzip; rebuild re-reads radicals
# from an existing gzip when present, else falls back to the module defaults.
OUT = ROOT / "stackchan_mcp" / "data" / "char_structure.json.gz"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    ids_path = Path(sys.argv[1])
    text = ids_path.read_text(encoding="utf-8")
    radicals = dict(_FALLBACK_RADICALS)
    if OUT.is_file():
        prev = json.loads(gzip.decompress(OUT.read_bytes()).decode("utf-8"))
        radicals.update(prev.get("radicals") or {})
    structures = [
        "左右",
        "上下",
        "左中右",
        "上中下",
        "半包圍",
        "全包圍",
        "品字",
        "交疊",
        "單一",
    ]
    code = {name: str(i) for i, name in enumerate(structures)}
    entries: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        ch, ids = parts[1], parts[2]
        if len(ch) != 1:
            continue
        o = ord(ch)
        if not (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF):
            continue
        ids0 = ids.split(";")[0].strip()
        if ids0 and ids0[0] in IDS_STRUCTURE:
            struct = IDS_STRUCTURE[ids0[0]]
            rest = ids0[1:]
            rad = ""
            if rest and rest[0] not in IDS_STRUCTURE and rest[0] != "&":
                cand = rest[0]
                if cand in radicals:
                    rad = cand
            entries[ch] = code[struct] + rad
        elif len(ids0) == 1 and ids0 == ch:
            entries[ch] = code["單一"]
    payload = {
        "version": 1,
        "source": f"cjkvi IDS from {ids_path.name}",
        "structures": structures,
        "radicals": radicals,
        "entries": entries,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wb", compresslevel=9) as handle:
        handle.write(raw)
    print(f"wrote {OUT} entries={len(entries)} raw={len(raw)} gz={OUT.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
