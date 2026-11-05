# SPDX-License-Identifier: BSL-1.0
"""python3 -m unittest tools/lingling_assets/test_build_pack.py"""

import struct
import sys
import tempfile
import unittest
import wave
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_pack as bp  # noqa: E402


def fake_png(w=240, h=240) -> bytes:
    ihdr = struct.pack(">II5B", w, h, 8, 2, 0, 0, 0)
    return bp.PNG_SIG + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0" * 4


class BuildPackTest(unittest.TestCase):
    def test_layout_matches_firmware(self):
        pack = bp.build([("face.f2.happy", 1, 0, fake_png()), ("vo.intro.1", 2, 16000, b"\x01\x00" * 3)])
        magic, ver, count, size, res = struct.unpack_from("<4sHHII", pack)
        self.assertEqual((magic, ver, count, size, res), (b"LLAP", 1, 2, len(pack), 0))
        self.assertEqual(bp.ENTRY.size, 52)
        name, off, n, kind, r2, rate, crc = struct.unpack_from("<32sIIHHII", pack, 16 + 52)
        self.assertEqual(name.rstrip(b"\0"), b"vo.intro.1")
        self.assertEqual((kind, rate, n, off % 4), (2, 16000, 6, 0))
        self.assertEqual(crc, zlib.crc32(pack[off:off + n]))
        self.assertEqual([e["name"] for e in bp.parse(pack)], ["face.f2.happy", "vo.intro.1"])

    def test_rejects_duplicates_long_names_and_oversize(self):
        with self.assertRaises(SystemExit):
            bp.build([("a", 1, 0, b"x"), ("a", 1, 0, b"y")])
        with self.assertRaises(SystemExit):
            bp.build([("x" * 32, 1, 0, b"x")])
        with self.assertRaises(SystemExit):
            bp.build([("big", 2, 16000, b"\0" * (bp.MAX_SIZE + 2))])

    def test_collect_names_and_validation(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "f3_sleepy.png").write_bytes(fake_png())
            (d / "neutral.png").write_bytes(fake_png())
            names = [i[0] for i in bp.collect_images(d)]
            self.assertEqual(sorted(names), ["face.f3.sleepy", "face.neutral"])
            (d / "f1_grumpy.png").write_bytes(fake_png())
            with self.assertRaises(SystemExit):
                bp.collect_images(d)
            (d / "f1_grumpy.png").unlink()
            (d / "f1_happy.png").write_bytes(fake_png(120, 120))
            with self.assertRaises(SystemExit):
                bp.collect_images(d)

    def test_wav_clip(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "vo.evolve.f2.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x10\x00" * 100)
            (name, kind, rate, data), = bp.collect_clips(Path(t), 16000)
            self.assertEqual((name, kind, rate, len(data)), ("vo.evolve.f2", 2, 16000, 200))


if __name__ == "__main__":
    unittest.main()
