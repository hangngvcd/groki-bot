#!/usr/bin/env python3
"""Generate synthetic 「小白」 positive clips (16 kHz mono s16 WAV) with edge-tts + ffmpeg.
Usage: python gen_positive.py OUT_DIR
Variants: voice x text x rate x pitch, then clean / noise(SNR~10dB) / reverb copies."""

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

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
VOICES = {
    "hk": ["zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural"],
    "cn": [
        "zh-CN-XiaoxiaoNeural",
        "zh-CN-XiaoyiNeural",
        "zh-CN-YunxiaNeural",
        "zh-CN-YunxiNeural",
    ],
    "tw": ["zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural"],
}
TEXTS = {"t1": "小白", "t2": "小白小白", "t3": "小白，我想做功課"}
RATES = ["-15%", "+0%", "+20%"]
PITCHES = ["+0Hz", "+40Hz"]  # +40Hz ~ higher, more child-like
SEM = asyncio.Semaphore(4)


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


async def one(group, voice, tk, text, ri, rate, pi, pitch):
    base = f"{group}__{voice.split('-')[2]}__{tk}__r{ri}p{pi}"
    clean = OUT / f"{base}__clean.wav"
    if clean.exists():
        return
    mp3 = OUT / f"{base}.mp3"
    async with SEM:
        for attempt in range(6):
            try:
                await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(
                    str(mp3)
                )
                if mp3.stat().st_size > 1000:
                    break
            except Exception as e:
                print("retry", base, type(e).__name__)
            await asyncio.sleep(2 + 3 * attempt)
        else:
            print("FAILED", base)
            mp3.unlink(missing_ok=True)
            return
    # 0.5 s lead silence, 16k mono s16
    await asyncio.to_thread(
        ff,
        "-i",
        str(mp3),
        "-af",
        "adelay=500:all=1",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(clean),
    )
    # noisy: pink noise mixed ~10 dB below speech (loudnorm-free approximation)
    await asyncio.to_thread(
        ff,
        "-i",
        str(clean),
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=color=pink:amplitude=0.03:sample_rate=16000",
        "-filter_complex",
        "[0][1]amix=inputs=2:duration=first:normalize=0",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(OUT / f"{base}__noise.wav"),
    )
    # reverb-ish room: multi-tap echo + slight lowpass (far-field mic)
    await asyncio.to_thread(
        ff,
        "-i",
        str(clean),
        "-af",
        "aecho=0.8:0.7:40|70|110:0.4|0.3|0.2,lowpass=f=6000",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(OUT / f"{base}__reverb.wav"),
    )
    mp3.unlink()


async def main():
    jobs = [
        one(g, v, tk, t, ri, r, pi, p)
        for g, vs in VOICES.items()
        for v in vs
        for tk, t in TEXTS.items()
        for ri, r in enumerate(RATES)
        for pi, p in enumerate(PITCHES)
    ]
    await asyncio.gather(*jobs)
    print("clips:", len(list(OUT.glob("*.wav"))))


asyncio.run(main())
