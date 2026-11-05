# SPDX-License-Identifier: MIT
"""Offline 部首 / 結構 hints for show_character (dictation mode).

Derived from cjkvi IDS; see ``data/README.md``. Lookup never invents: missing
chars return an empty dict so the voice agent only encourages.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DATA = Path(__file__).with_name("data") / "char_structure.json.gz"

# IDS operator → HK structure label (also used when parsing raw ids.txt).
IDS_STRUCTURE: dict[str, str] = {
    "⿰": "左右",
    "⿱": "上下",
    "⿲": "左中右",
    "⿳": "上中下",
    "⿴": "全包圍",
    "⿵": "半包圍",
    "⿶": "半包圍",
    "⿷": "半包圍",
    "⿸": "半包圍",
    "⿹": "半包圍",
    "⿺": "半包圍",
    "⿻": "交疊",
}

# Built-in fallback nicknames if the data file's radicals map is unavailable.
_FALLBACK_RADICALS: dict[str, str] = {
    "氵": "三點水",
    "扌": "提手旁",
    "木": "木字旁",
    "火": "火字旁",
    "灬": "四點火",
    "土": "土字旁",
    "口": "口字旁",
    "忄": "豎心旁",
    "心": "心字底",
    "亻": "單人旁",
    "刂": "立刀旁",
    "辶": "走之底",
    "艹": "草字頭",
    "竹": "竹字頭",
    "⺮": "竹字頭",
    "纟": "絞絲旁",
    "糸": "絞絲旁",
    "糹": "絞絲旁",
    "钅": "金字旁",
    "金": "金字旁",
    "釒": "金字旁",
    "讠": "言字旁",
    "言": "言字旁",
    "訁": "言字旁",
    "饣": "食字旁",
    "食": "食字旁",
    "飠": "食字旁",
    "衤": "衣字旁",
    "礻": "示字旁",
    "王": "王字旁",
    "石": "石字旁",
    "月": "月字旁",
    "日": "日字旁",
    "虫": "虫字旁",
    "犭": "反犬旁",
    "疒": "病字旁",
    "宀": "寶蓋頭",
    "广": "廣字頭",
    "冫": "兩點水",
    "彳": "雙人旁",
    "攵": "反文旁",
    "禾": "禾字旁",
    "米": "米字旁",
    "女": "女字旁",
    "子": "子字旁",
    "阝": "左耳旁",
    "門": "門字框",
    "雨": "雨字頭",
}


def _data_path() -> Path:
    override = os.getenv("STACKCHAN_CHAR_STRUCTURE_PATH", "").strip()
    return Path(override) if override else _DEFAULT_DATA


def _load_json_bytes(raw: bytes) -> dict[str, Any]:
    return json.loads(raw.decode("utf-8"))


def _parse_ids_txt(text: str) -> dict[str, Any]:
    """Build the same schema from a raw cjkvi ids.txt (optional runtime path)."""
    radicals = dict(_FALLBACK_RADICALS)
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
    return {
        "version": 1,
        "structures": structures,
        "radicals": radicals,
        "entries": entries,
    }


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    path = _data_path()
    try:
        if not path.is_file():
            logger.warning("char structure data missing: %s", path)
            return {
                "structures": list(IDS_STRUCTURE.values()),
                "radicals": dict(_FALLBACK_RADICALS),
                "entries": {},
            }
        raw = path.read_bytes()
        name = path.name.lower()
        if name.endswith(".txt") or name == "ids.txt":
            return _parse_ids_txt(raw.decode("utf-8", errors="replace"))
        if name.endswith(".gz"):
            raw = gzip.decompress(raw)
        return _load_json_bytes(raw)
    except Exception:
        logger.exception("failed to load char structure data from %s", path)
        return {
            "structures": list(IDS_STRUCTURE.values()),
            "radicals": dict(_FALLBACK_RADICALS),
            "entries": {},
        }


def reload_char_structure() -> None:
    """Drop the cached table (tests / STACKCHAN_CHAR_STRUCTURE_PATH changes)."""
    _table.cache_clear()


def lookup_char_structure(character: str) -> dict[str, str]:
    """Return ``structure`` / ``radical`` for a single CJK char when known.

    Empty dict when the character is missing, ambiguous, or data unavailable.
    Never invents values.
    """
    if not isinstance(character, str) or len(character) != 1:
        return {}
    table = _table()
    packed = table.get("entries", {}).get(character)
    if not isinstance(packed, str) or not packed:
        return {}
    structures = table.get("structures") or []
    radicals = table.get("radicals") or _FALLBACK_RADICALS
    try:
        idx = int(packed[0])
        structure = structures[idx]
    except (ValueError, IndexError, TypeError):
        return {}
    out: dict[str, str] = {"structure": structure}
    if len(packed) > 1:
        rad_ch = packed[1]
        name = radicals.get(rad_ch) or _FALLBACK_RADICALS.get(rad_ch)
        if name:
            out["radical"] = name
    return out
