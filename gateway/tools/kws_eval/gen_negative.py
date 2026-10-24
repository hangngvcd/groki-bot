#!/usr/bin/env python3
"""Generate ~30 min of everyday household/TV speech without 「小白」 (near words included),
plus noise/music, as 16 kHz mono s16 WAV.  Usage: python gen_negative.py CORPUS.txt OUT_DIR"""

import asyncio
import subprocess
import sys
from pathlib import Path
import shutil

try:
    import edge_tts
except ImportError:
    raise SystemExit(
        "edge-tts is required: run with uv run --with edge-tts python tools/kws_eval/"
        + Path(__file__).name
    )
if shutil.which("ffmpeg") is None:
    raise SystemExit(
        "ffmpeg is required; install it locally before generating synthetic clips."
    )

corpus = [
    line.strip()
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    if line.strip()
]
assert not any("小白" in line for line in corpus)
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
READS = [
    ("zh-HK-HiuGaaiNeural", "+0%"),
    ("zh-HK-HiuGaaiNeural", "+15%"),
    ("zh-HK-HiuMaanNeural", "+0%"),
    ("zh-HK-HiuMaanNeural", "-10%"),
    ("zh-HK-WanLungNeural", "+0%"),
    ("zh-HK-WanLungNeural", "+15%"),
    ("zh-CN-XiaoxiaoNeural", "+0%"),
]  # Mandarin TV / news
CHUNK = 8
SEM = asyncio.Semaphore(3)


def ff(*a):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *a], check=True)


async def chunk(voice, rate, idx, lines):
    wav = OUT / f"part__{voice.split('-')[2]}__{rate}__{idx:02d}.wav"
    if wav.exists():
        return wav
    mp3 = wav.with_suffix(".mp3")
    async with SEM:
        for attempt in range(8):
            try:
                await edge_tts.Communicate("\n".join(lines), voice, rate=rate).save(
                    str(mp3)
                )
                if mp3.stat().st_size > 1000:
                    break
            except Exception as e:
                print("retry", wav.name, type(e).__name__)
            await asyncio.sleep(2 + 3 * attempt)
        else:
            print("FAILED", wav.name)
            return None
    await asyncio.to_thread(
        ff,
        "-i",
        str(mp3),
        "-af",
        "apad=pad_dur=0.7",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(wav),
    )
    mp3.unlink()
    return wav


async def main():
    for voice, rate in READS:
        parts = await asyncio.gather(
            *[
                chunk(voice, rate, i // CHUNK, corpus[i : i + CHUNK])
                for i in range(0, len(corpus), CHUNK)
            ]
        )
        parts = [p for p in parts if p]
        lst = OUT / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        name = OUT / f"speech__{voice.split('-')[2]}__{rate}.wav"
        ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(name))
        for p in parts:
            p.unlink()
    # one read with background pink noise (TV hiss / fan)
    src = OUT / "speech__HiuGaaiNeural__+0%.wav"
    ff(
        "-i",
        str(src),
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=color=pink:amplitude=0.03:sample_rate=16000",
        "-filter_complex",
        "[0][1]amix=inputs=2:duration=first:normalize=0",
        "-c:a",
        "pcm_s16le",
        str(OUT / "speechnoise__HiuGaai.wav"),
    )
    # 2 min pink/brown noise + 2 min synthetic 'music' (chord arpeggio with tremolo)
    ff(
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=color=brown:amplitude=0.2:duration=120:sample_rate=16000",
        "-c:a",
        "pcm_s16le",
        str(OUT / "noise__brown.wav"),
    )
    ff(
        "-f",
        "lavfi",
        "-i",
        "aevalsrc='0.15*sin(2*PI*(220+110*mod(floor(t*4),4))*t)+0.1*sin(2*PI*330*t)*sin(2*PI*0.5*t)+0.05*sin(2*PI*660*t)':s=16000:d=120",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(OUT / "music__synth.wav"),
    )
    (OUT / "list.txt").unlink(missing_ok=True)


asyncio.run(main())
