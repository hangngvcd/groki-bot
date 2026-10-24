# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tools.check_ota_headroom import ota_slot_size, parse_size


class PartitionTest(unittest.TestCase):
    def test_sizes(self):
        for value in ('0x400000', '4194304', '4096K', '4M', '4m'):
            self.assertEqual(parse_size(value), 4194304)
        for value in ('', '-1', '4G', 'garbage'):
            with self.assertRaises(ValueError):
                parse_size(value)

    def test_csv(self):
        self.assertEqual(ota_slot_size('# comment\n\nother,app,ota_1,,1M\nota_0, app, ota_0, 0x10000, 4M,'), 4194304)
        for text in ('', 'ota_0,data,ota_0,,4M', 'ota_0,app,ota_0,,0'):
            with self.assertRaises(ValueError):
                ota_slot_size(text)

    def test_threshold_and_summary(self):
        script = Path(__file__).with_name('check_ota_headroom.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'stackchan_idf.bin'
            partitions = root / 'partitions.csv'
            summary = root / 'summary'
            partitions.write_text('ota_0,app,ota_0,,1000,\n')
            for size, minimum, status in ((900, 10, 0), (901, 10, 1), (999, 0, 0), (1001, 0, 1)):
                image.write_bytes(b'0' * size)
                result = subprocess.run(
                    [sys.executable, str(script), '--bin', str(image),
                     '--partitions', str(partitions), '--board', 'spotpear128cam',
                     '--min-free-pct', str(minimum)],
                    env={**os.environ, 'GITHUB_STEP_SUMMARY': str(summary)},
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn(f'spotpear128cam: app {size} / slot 1000', result.stdout)
                self.assertIn(result.stdout.strip(), summary.read_text())


if __name__ == '__main__':
    unittest.main()
