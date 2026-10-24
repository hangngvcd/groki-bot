# SPDX-License-Identifier: MIT
"""Static guard (ticket 15d): no NVS / flash access from PSRAM-stack tasks.

ESP-IDF asserts in spi_flash_disable_interrupts_caches_and_other_cpu
(cache_utils.c, esp_task_stack_is_sane_cache_disabled()) when a flash op runs
on a task whose stack is in external RAM. On 01ad5b6 the conversation task
(PSRAM stack) wrote lingling/intro_done after session ready -> panic loop.

Rule: a source file that creates a task with a MALLOC_CAP_SPIRAM stack must not
call NVS / partition / flash / OTA APIs directly; it goes through
main/flash_worker (internal-RAM-stack executor). Files where the flash op
provably runs on a different, internal-stack task are allow-listed with a reason.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / 'main', ROOT / 'components']
SKIP_PARTS = {'M5Unified', 'M5GFX', 'test', 'build', 'third_party'}

PSRAM_TASK = re.compile(
    r'xTaskCreate\w*WithCaps\s*\([^;]*?MALLOC_CAP_SPIRAM'
    r'|xTaskCreateStatic\w*\s*\(',
    re.S)
CAPS_VAR = re.compile(r'kCaps\s*=\s*MALLOC_CAP_SPIRAM')
FLASH_CALL = re.compile(
    r'\b(nvs_(open|get_\w+|set_\w+|commit|erase_\w+)|esp_partition_(read|write|erase_range)\w*'
    r'|esp_flash_(read|write|erase)\w*|spi_flash_(read|write|erase)\w*|esp_ota_(begin|write|end)\w*)\s*\(')

# file -> reason the flash call does NOT run on the PSRAM task in that file.
ALLOW = {
    'components/wifi_config_service/release_ota.cpp':
        'esp_ota_* runs in the internal-stack "release-ota" worker; only the '
        'read-only "vfetch" JSON fetch task has a PSRAM stack',
    'main/asr_probe.cpp': 'diagnostic probe; PSRAM-stack tasks only feed audio',
    'main/flash_worker.cpp': 'the internal-stack executor itself',
}


def strip_comments(text: str) -> str:
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def creates_psram_task(code: str) -> bool:
    if PSRAM_TASK.search(code) and ('MALLOC_CAP_SPIRAM' in code):
        return True
    return bool(CAPS_VAR.search(code) and re.search(r'xTaskCreate\w*WithCaps', code))


def offenders():
    out = []
    for base in SCAN:
        for path in base.rglob('*.c*'):
            if path.suffix not in ('.c', '.cpp', '.cc') or SKIP_PARTS & set(path.parts):
                continue
            rel = path.relative_to(ROOT).as_posix()
            code = strip_comments(path.read_text(errors='replace'))
            if not creates_psram_task(code) or rel in ALLOW:
                continue
            calls = sorted({m.group(1) for m in FLASH_CALL.finditer(code)})
            if calls:
                out.append(f'{rel}: {", ".join(calls)}')
    return out


class FlashStackGuard(unittest.TestCase):
    def test_no_flash_calls_in_psram_stack_task_files(self):
        self.assertEqual(offenders(), [], 'route these through main/flash_worker.hpp')

    def test_conversation_task_is_psram_and_uses_worker(self):
        code = strip_comments((ROOT / 'main/conversation_task.cpp').read_text())
        self.assertTrue(creates_psram_task(code))
        self.assertIn('flash_worker::nvs_write_u8', code)
        self.assertIn('flash_worker::nvs_read_u8', code)

    def test_worker_stack_is_internal(self):
        code = strip_comments((ROOT / 'main/flash_worker.cpp').read_text())
        self.assertNotIn('MALLOC_CAP_SPIRAM', code)
        self.assertNotIn('WithCaps', code)

    def test_guard_detects_regression(self):
        bad = ('void f(){ xTaskCreatePinnedToCoreWithCaps(t,"x",8192,0,5,0,0,MALLOC_CAP_SPIRAM);'
               ' nvs_set_u8(h,"k",1); nvs_commit(h); }')
        self.assertTrue(creates_psram_task(bad))
        self.assertTrue(FLASH_CALL.search(bad))


if __name__ == '__main__':
    unittest.main()
