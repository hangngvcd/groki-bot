#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0
"""Report app OTA slot headroom and optionally enforce a minimum percentage."""
import argparse
import csv
import os
from pathlib import Path
import re


def parse_size(value: str) -> int:
    match = re.fullmatch(r'(0[xX][0-9a-fA-F]+|[0-9]+)\s*([kKmM]?)', value.strip())
    if not match:
        raise ValueError(f'Invalid partition size: {value}')
    number, unit = match.groups()
    size = int(number, 16 if number.lower().startswith('0x') else 10)
    return size * {'': 1, 'K': 1024, 'M': 1024 * 1024}[unit.upper()]


def ota_slot_size(text: str) -> int:
    for row in csv.reader(line for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')):
        if len(row) >= 5 and row[1].strip() == 'app' and row[2].strip() == 'ota_0':
            size = parse_size(row[4])
            if size > 0:
                return size
    raise ValueError('No positive ota_0 app slot found')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', type=Path, required=True)
    parser.add_argument('--partitions', type=Path, required=True)
    parser.add_argument('--board')
    parser.add_argument('--min-free-pct', type=float, default=0)
    args = parser.parse_args()
    if not 0 <= args.min_free_pct <= 100:
        parser.error('--min-free-pct must be between 0 and 100')
    slot = ota_slot_size(args.partitions.read_text())
    app = args.bin.stat().st_size
    free = slot - app
    pct = free * 100 / slot
    board = args.board or args.bin.parent.name.removeprefix('build-')
    report = f'{board}: app {app} / slot {slot}, free {free} ({pct:.2f}%)'
    print(report)
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a') as out:
            out.write(report + '\n')
    return int(pct < args.min_free_pct)


if __name__ == '__main__':
    raise SystemExit(main())
