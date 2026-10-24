# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0
import pathlib
import re
import unittest
from script.release_board import KNOWN_SLUGS, board_for_asset


class ReleaseBoardTest(unittest.TestCase):
    def test_matrix(self):
        text = (pathlib.Path(__file__).resolve().parents[1] / '.github/workflows/release.yml').read_text()
        matrix = re.search(r'^        board: \[([^]]+)\]', text, re.M)
        self.assertIsNotNone(matrix)
        self.assertEqual(set(KNOWN_SLUGS), set(x.strip() for x in matrix[1].split(',')))

    def test_assets(self):
        for slug in KNOWN_SLUGS:
            for tag in ('v1.2.3', 'v1.2.3-rc.1', 'dev-12345678'):
                self.assertEqual(board_for_asset(f'firmware-{tag}-{slug}.zip'), slug)
        self.assertEqual(board_for_asset('firmware-v1.2.3.zip'), 'cores3')
        for name in ('firmware-v1.2.3-unknown.zip', 'firmware-dev.zip', 'firmware-v1.2.3-spotpear128cam.bin', 'other-v1.2.3.zip', '../firmware-v1.2.3.zip', 'firmware--cores3.zip'):
            self.assertIsNone(board_for_asset(name), name)


if __name__ == '__main__':
    unittest.main()
