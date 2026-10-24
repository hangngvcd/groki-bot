#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
voice=Sinji
dry_run=0
output=
while (($#)); do
    case "$1" in
        --voice) voice="${2:?--voice requires a voice}"; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --help|-h) echo "Usage: $0 [--voice Sinji] [--dry-run] OUTPUT_DIR"; exit 0 ;;
        -*) echo "Unknown option: $1" >&2; exit 2 ;;
        *) output="$1"; shift ;;
    esac
done
[[ -n "$output" ]] || { echo "OUTPUT_DIR required" >&2; exit 2; }
if ((!dry_run)); then
    [[ "$(uname -s)" == Darwin ]] || { echo "Requires macOS say and afconvert" >&2; exit 2; }
    mkdir -p "$output"
fi
lines=(
    '今日有咩功課？'
    '仲有咩未做？'
    '數學做完喇'
    '唔係'
    '數學做完喇'
    '係'
    '幫我話俾媽媽知我數學卡住'
    '係'
    '幫我改吓啱唔啱，俾個分我'
)
run() {
    if ((dry_run)); then
        printf '%s ' "$1"; shift
        for arg in "$@"; do printf '%q ' "$arg"; done
        printf '\n'
    else
        "$@"
    fi
}
for index in "${!lines[@]}"; do
    printf -v stem '%02d-homework' "$((index + 1))"
    # Intermediate AIFF is synthetic too; only WAVs are passed to the probe.
    run say -v "$voice" -o "$output/$stem.aiff" "${lines[$index]}"
    run afconvert -f WAVE -d LEI16@16000 -c 1 "$output/$stem.aiff" "$output/$stem.wav"
    if ((!dry_run)); then rm "$output/$stem.aiff"; fi
done
