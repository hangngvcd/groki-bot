#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0
"""Classify release ZIPs without guessing an unknown board."""
import re
import sys

KNOWN_SLUGS = ('cores3', 'atoms3r', 'atoms3', 'stopwatch', 'spotpear128cam')


def board_for_asset(name: str) -> str | None:
    for slug in KNOWN_SLUGS:
        if re.fullmatch(r'firmware-[A-Za-z0-9][A-Za-z0-9._-]*-' + slug + r'\.zip', name):
            return slug
    # Only un-suffixed stable version names are legacy CoreS3 releases.
    # Unknown board suffixes must never be interpreted as prerelease tags.
    if re.fullmatch(r'firmware-v[0-9]+\.[0-9]+\.[0-9]+\.zip', name):
        return 'cores3'
    return None


if __name__ == '__main__':
    board = board_for_asset(sys.argv[1]) if len(sys.argv) == 2 else None
    if board is None:
        sys.exit(1)
    print(board)
