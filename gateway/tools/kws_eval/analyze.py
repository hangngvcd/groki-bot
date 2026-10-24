#!/usr/bin/env python3
"""Summarise eval_kws.py results.json into markdown tables.  Usage: python analyze.py results.json"""

import json
import sys

res = json.load(open(sys.argv[1]))


def rate(hits, pred):
    v = [h for k, h in hits.items() if pred(k.split("|")[0].split("__"))]
    return (100.0 * sum(v) / len(v)) if v else float("nan"), len(v)


rows = []
for r in sorted(res, key=lambda r: (r["variant"], r["thr"], r["score"])):
    long_ = "long" in r["variant"]
    tgt = (
        (lambda g: len(g) > 2 and g[2] == "t2") if long_ else (lambda g: True)
    )  # long variant only expected on 小白小白
    row = {"v": r["variant"], "thr": r["thr"], "score": r["score"]}
    for grp in ("hk", "cn", "tw"):
        row[grp] = rate(r["hits"], lambda g, grp=grp: g[0] == grp and tgt(g))[0]
    for t in ("t1", "t2", "t3"):
        row["hk_" + t] = rate(
            r["hits"], lambda g, t=t: g[0] == "hk" and len(g) > 2 and g[2] == t
        )[0]
    for c in ("clean", "noise", "reverb"):
        row["hk_" + c] = rate(
            r["hits"],
            lambda g, c=c: (
                g[0] == "hk" and tgt(g) and len(g) > 4 and g[4].startswith(c)
            ),
        )[0]
    f = r["falses"]
    row["fa_canto"] = sum(
        len(v) for k, v in f.items() if k.startswith("speech") and "Xiaoxiao" not in k
    )
    row["fa_mand"] = sum(len(v) for k, v in f.items() if "Xiaoxiao" in k)
    row["fa_noise"] = sum(
        len(v) for k, v in f.items() if k.startswith(("noise", "music"))
    )
    row["fa_total"] = sum(len(v) for v in f.values())
    row["fa_per_h"] = (
        row["fa_total"] / (r["neg_s"] / 3600) if r["neg_s"] else float("nan")
    )
    rows.append(row)
neg_min = res[0]["neg_s"] / 60
print(f"Negative audio: {neg_min:.1f} min total\n")
print(
    "| variant | thr | score | HK hit% | CN hit% | TW hit% | HK「小白」 | HK「小白小白」 | HK「小白，我想做功課」 | HK clean/noise/reverb | false: Canto / Mand / noise+music | false per hour |"
)
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print(
        f"| {r['v']} | {r['thr']} | {r['score']:g} | {r['hk']:.0f} | {r['cn']:.0f} | {r['tw']:.0f} | {r['hk_t1']:.0f} | {r['hk_t2']:.0f} | {r['hk_t3']:.0f} | "
        f"{r['hk_clean']:.0f}/{r['hk_noise']:.0f}/{r['hk_reverb']:.0f} | {r['fa_canto']} / {r['fa_mand']} / {r['fa_noise']} | {r['fa_per_h']:.1f} |"
    )
if len(sys.argv) > 2:
    for r in res:
        if (
            r["variant"] == sys.argv[2]
            and r["thr"] == float(sys.argv[3])
            and r["score"] == float(sys.argv[4])
        ):
            print(json.dumps(r["falses"], ensure_ascii=False))
