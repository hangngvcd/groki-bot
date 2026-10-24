#!/usr/bin/env python3
"""Offline KWS sweep for 「小白」 using the gateway's own wake_gate.SherpaOnnxKeywordSpotter
(same code path as gateway/scripts/kws_offline_repro.py: 100 ms chunks, +1 s trailing silence).

Run from gateway/:  uv run --extra wakeword python tools/kws_eval/eval_kws.py \
    --pos DIR --neg DIR --out results.json [--thresholds ...] [--scores ...]
Each worker uses its own symlinked model dir, because the spotter writes stackchan_keywords.txt there."""

import argparse
import json
import logging
import os
import tempfile
import time
import wave
from multiprocessing import Pool
from pathlib import Path
from stackchan_mcp.wake_gate import (
    _model_dir_from_env,
    _resolve_model_dir,
    parse_keyword_variants,
)

VARIANTS = {
    "V1_mandarin": ("x iǎo b ái @小白", "小白"),
    "V2_flat_tone": ("x iāo b ái @小白", "小白"),
    "V3_canto_siu_bai": ("s iū b ái @小白", "小白"),
    "V4_canto_siu_ba": ("s iū b à @小白", "小白"),
    "V5_canto_en_phones": ("S IY1 UW0 B AA1 K @小白", "小白"),
    "V6_long_mandarin": ("x iǎo b ái x iǎo b ái @小白小白", "小白小白"),
    # combined: per-keyword ":boost #threshold" overrides (sherpa-onnx keywords-file syntax); the
    # gateway accepts both pipes and embedded newlines between keyword variants.
    "F1_combo_mand1_cantolong": (
        "x iǎo b ái :1 #0.02 @小白\nS UW1 B AA1 S UW1 B AA1 :10 #0.02 @小白小白",
        "小白",
    ),
    "F2_inline_E4": ("S UW1 B AA1 S UW1 B AA1 :10 #0.02 @小白小白", "小白小白"),
    "V7_long_canto": ("s iū b ái s iū b ái @小白小白", "小白小白"),
}
MODEL = _model_dir_from_env()
CHUNK = 3200  # 100 ms @16k s16


def read(p):
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (
            1,
            16000,
            2,
        ), p
        return w.readframes(w.getnframes())


_cache = {}


def pcm(p):
    if p not in _cache:
        _cache[p] = read(p)
    return _cache[p]


def run(job):
    from stackchan_mcp.wake_gate import SherpaOnnxKeywordSpotter

    logging.disable(logging.INFO)
    vname, kw, phrase, thr, score, pos, neg, gaps = job
    with tempfile.TemporaryDirectory(prefix="kwsdir-") as tmp:
        d = Path(tmp)
        for f in _resolve_model_dir(MODEL).iterdir():
            if f.is_file() and f.name != "stackchan_keywords.txt":
                (d / f.name).symlink_to(f.resolve())
        sp = SherpaOnnxKeywordSpotter(
            model_dir=d,
            keyword=kw,
            phrase=phrase,
            keywords_threshold=thr,
            keywords_score=score,
            num_threads=1,
        )
        # Gateway-like: one continuous stream (no per-clip reset), clips separated by a low noise
        # floor, periodic 60 s reset as in DORMANT.  Two passes with different gap lengths so the
        # model's chunk phase differs; hit = detection between clip start and clip end + 1.5 s.
        import numpy as np

        rng = np.random.default_rng(0)
        hits = {}
        for gap_s in gaps:
            sp.reset()
            n_chunks = 0
            for p in pos:
                gap = rng.normal(0, 60, int(16000 * gap_s)).astype("<i2").tobytes()
                data = pcm(p)
                seg = gap + data + gap[: 16000 * 3]
                clip_from = len(gap) // CHUNK
                clip_to = (len(gap) + len(data)) // CHUNK + 15
                hit = False
                for j, o in enumerate(range(0, len(seg) - len(seg) % CHUNK, CHUNK)):
                    n_chunks += 1
                    if n_chunks % 600 == 0:
                        sp.reset()
                    if sp.detect(seg[o : o + CHUNK]) and clip_from <= j <= clip_to:
                        hit = True
                hits[f"{Path(p).name}|gap{gap_s}"] = hit
        falses, neg_s = {}, 0.0
        for p in neg:
            sp.reset()
            data = pcm(p) + b"\0" * 32000
            times = []
            for i, o in enumerate(range(0, len(data), CHUNK)):
                if i and i % 600 == 0:
                    sp.reset()  # gateway resets the stream every 60 s while DORMANT
                if sp.detect(data[o : o + CHUNK]):
                    times.append(round(i * 0.1, 1))
            falses[Path(p).name] = times
            neg_s += len(data) / 32000
    return {
        "variant": vname,
        "thr": thr,
        "score": score,
        "hits": hits,
        "falses": falses,
        "neg_s": neg_s,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos", type=Path)
    ap.add_argument("--neg", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--thresholds", nargs="*", type=float, default=[0.02, 0.1])
    ap.add_argument("--scores", nargs="*", type=float, default=[1, 3, 7])
    ap.add_argument(
        "--custom",
        nargs="*",
        default=[],
        help="extra variants NAME=KEYWORD (phrase = text after @)",
    )
    ap.add_argument("--pos-glob", default="*.wav")
    ap.add_argument("--gaps", nargs="*", type=float, default=[2.0, 2.35])
    ap.add_argument("--procs", type=int, default=os.cpu_count())
    a = ap.parse_args()
    for c in a.custom:
        n, kw = c.split("=", 1)
        VARIANTS[n] = (kw, parse_keyword_variants(kw)[0].tag)
        a.variants.append(n) if n not in a.variants else None
    for name in a.variants:
        if name not in VARIANTS:
            ap.error(f"unknown variant: {name}")
        parse_keyword_variants(VARIANTS[name][0])
    pos = sorted(str(p) for p in a.pos.glob(a.pos_glob)) if a.pos else []
    neg = sorted(str(p) for p in a.neg.glob("*.wav")) if a.neg else []
    jobs = [
        (v, *VARIANTS[v], t, s, pos, neg, a.gaps)
        for v in a.variants
        for t in a.thresholds
        for s in a.scores
    ]
    t0 = time.time()
    with Pool(a.procs) as pool:
        res = []
        for i, r in enumerate(pool.imap_unordered(run, jobs), 1):
            res.append(r)
            with open(str(a.out) + "l", "a") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(
                f"{i}/{len(jobs)} {r['variant']} thr={r['thr']} score={r['score']} "
                f"hit={sum(r['hits'].values())}/{len(r['hits'])} false={sum(len(v) for v in r['falses'].values())} ({time.time() - t0:.0f}s)",
                flush=True,
            )
    a.out.write_text(json.dumps(res, ensure_ascii=False))
