# Local wake-word evaluation

Run from `gateway/` with the existing Mandarin+English KWS model in
`models/kws/` (or set `STACKCHAN_KWS_MODEL_DIR`). The sweep uses the gateway
spotter and variant parser, continuous 100 ms chunks, 60-second resets and
two gap offsets to reduce streaming phase bias. `--custom 'NAME=line|line'`
accepts full keyword lines including per-line `:score #threshold` overrides.

For a Mac real-voice test, obtain parental consent and record about ten takes
per sentence, with one second of silence before speaking. Keep everything in
an ignored local temporary folder, for example `kws-recordings-local/pos` and
`kws-recordings-local/neg`. Never commit or upload recordings.

Use QuickTime **New Audio Recording**, save locally as M4A, then convert:

```bash
mkdir -p kws-recordings-local/pos kws-recordings-local/neg
afconvert -f WAVE -d LEI16@16000 -c 1 input.m4a kws-recordings-local/pos/hk__child__t2__r0p0__clean_01.wav
# Or record directly with ffmpeg (macOS microphone):
ffmpeg -f avfoundation -i ':0' -t 3 -ar 16000 -ac 1 kws-recordings-local/pos/hk__child__t2__r0p0__clean_02.wav
```

Names use `hk__` for Cantonese or `cn__` for Mandarin, then speaker, sentence,
take and condition separated by `__`. Sentence IDs for 鈴鈴 (default): `t1` = 鈴鈴,
`t2` = 喂，鈴鈴！, `t3` = 鈴鈴，我想做功課 (legacy 小白: 小白 / 小白小白 /
小白，我想做功課). Record 10–20 minutes of TV or household speech without
the wake word into `neg/tv.wav`, also 16 kHz mono signed 16-bit WAV.

## 鈴鈴 quick check (current default)

```bash
export STACKCHAN_WAKE_PHRASE=鈴鈴
uv run --extra wakeword python tools/kws_eval/eval_kws.py \
  --pos kws-recordings-local/pos --neg kws-recordings-local/neg \
  --out kws-recordings-local/results.json --thresholds 0.05 --scores 1 \
  --variants C1_l2s1_l4s1 C2_l2s1_l4s3 L1_ling2_ling2 L2_ling4_ling4
```

`C1_l2s1_l4s1` is the built-in preset. Synthetic results (Oct 2026, 972 positive
clips, 57 min of negatives that deliberately include 電鈴 / 鈴聲 / 美玲 / 零):

| Variant | Overall | HK | CN | TW | False triggers (57 min) |
|---|---|---|---|---|---|
| C1 `líng líng :1` + `lìng lìng :1` (default) | 85% | 69% | 93% | 96% | 10 |
| C2 `líng líng :1` + `lìng lìng :3` | 89% | 76% | 96% | 97% | 18 |
| L1 `líng líng :1` | 81% | 56% | 93% | 96% | 10 |
| L2 `lìng lìng :1` | 20% | 59% | 0% | 0% | 5 |

Homophones (玲玲, 零零, 007…) wake C1 on 32 of 49 synthetic sentences.

## Legacy 小白

```bash
export STACKCHAN_WAKE_PHRASE=小白
for f in kws-recordings-local/pos/*.wav; do
  uv run --extra wakeword python scripts/kws_offline_repro.py --per-variant --wav "$f"
done
uv run --extra wakeword python tools/kws_eval/eval_kws.py \
  --pos kws-recordings-local/pos --neg kws-recordings-local/neg \
  --out kws-recordings-local/results.json --thresholds 0.02 0.05 --scores 1 3 7 10 \
  --variants V1_mandarin V6_long_mandarin F1_combo_mand1_cantolong \
  --custom 'E4=S UW1 B AA1 S UW1 B AA1 @小白小白'
uv run python tools/kws_eval/analyze.py kws-recordings-local/results.json
```

Read the HK `t2` column for the combo, plus false triggers on negative audio.
Per-line overrides in the combo stay fixed even when sweeping global settings.

| Real Cantonese 小白小白 hit rate | Decision |
|---|---|
| ≥80%, zero TV false triggers | Use combo |
| 50–80% | Combo + BOOT button primary; speak close to the device |
| <50% | Mandarin 小白 (`x iǎo b ái :1 #0.02 @小白`) or button-only |

Keep BOOT available in every case. Ticket 04's synthetic 58–70% Cantonese and
98–99% Mandarin results do not establish real child performance. Delete all
recordings, converted copies, and results after testing (including QuickTime's
original files):

```bash
rm -r kws-recordings-local
```

Optional synthetic corpus generation needs **edge-tts** and **ffmpeg** on PATH.
edge-tts already belongs to the gateway dependencies; `uv run --with edge-tts`
also works in another environment. Generation calls the online TTS service;
use only the provided synthetic text, never private recordings. Evaluation
itself is offline and does not import edge-tts.

```bash
uv run --with edge-tts python tools/kws_eval/gen_positive.py kws-recordings-local/pos
uv run --with edge-tts python tools/kws_eval/gen_negative.py tools/kws_eval/neg_corpus.txt kws-recordings-local/neg
```
