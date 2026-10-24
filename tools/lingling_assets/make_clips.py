#!/usr/bin/env python3
# SPDX-License-Identifier: BSL-1.0
"""Synthesize 鈴鈴's local clips (lines.json) to mono s16le WAV files.

Uses the free edge-tts CLI/module (no API key) and ffmpeg. Run once on a dev
machine; the device only plays the resulting PCM from the asset pack.

    python3 tools/lingling_assets/make_clips.py OUT_DIR [--lines lines.json] [--python /path/to/python-with-edge-tts]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--lines", type=Path, default=HERE / "lines.json")
    ap.add_argument("--python", default=sys.executable, help="python that has edge_tts installed")
    a = ap.parse_args()
    cfg = json.loads(a.lines.read_text(encoding="utf-8"))
    rate = int(cfg.get("sample_rate", 16000))
    a.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in cfg["clips"].items():
            mp3 = Path(tmp) / f"{name}.mp3"
            subprocess.run(
                [a.python, "-m", "edge_tts", "--voice", cfg["voice"], f"--rate={cfg.get('rate', '+0%')}",
                 f"--pitch={cfg.get('pitch', '+0Hz')}", "--text", text, "--write-media", str(mp3)],
                check=True,
            )
            wav = a.out / f"{name}.wav"
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp3), "-af",
                 "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                 "silenceremove=start_periods=1:start_threshold=-50dB,areverse,volume=0.9",
                 "-ac", "1", "-ar", str(rate), "-sample_fmt", "s16", str(wav)],
                check=True,
            )
            print(f"{wav.name}: {wav.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
