# SPDX-License-Identifier: MIT
"""Dialogue tuning for the 鈴鈴 Grok voice session (spec: xiaoya-dialogue-fix-v1).

Pure helpers kept out of the bridge so they are easy to test: env tunables,
input energy, transcription keyterms, the opt-in transcript log, the reply
sentence cap and the Cantonese provider rules.
"""

from __future__ import annotations

from datetime import datetime
import logging
import math
import os
from pathlib import Path
import re
import struct
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

HKT = ZoneInfo("Asia/Hong_Kong")
MAX_KEYTERMS = 100
BASE_KEYTERMS = (
    "鈴鈴", "娜娜", "Elena",
    "中文", "英文", "數學", "常識", "普通話", "視藝", "音樂",
    "功課", "默書", "抄生字", "生字", "工作紙", "補充", "作文", "閱讀",
    "練習", "簿", "温書", "測驗", "背書", "改正", "簽名",
)
# Values the live probe (2026-10-24) showed grok-transcribe accepts. All gave the
# same Cantonese transcripts on synthetic HK speech; see NOTES.md.
_OMIT_HINT = {"", "auto", "omit", "none"}


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        logger.warning("ignoring invalid %s", name)
        value = default
    return max(lo, min(hi, value))


def env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        logger.warning("ignoring invalid %s", name)
        value = default
    if math.isnan(value):
        value = default
    return max(lo, min(hi, value))


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def transcription_config(language: str | None, keyterms: list[str] | None) -> dict:
    config: dict = {"model": "grok-transcribe"}
    hint = (language or "").strip()
    if hint.lower() not in _OMIT_HINT:
        config["language_hint"] = hint
    if keyterms:
        config["keyterms"] = list(keyterms)[:MAX_KEYTERMS]
    return config


def pcm16_dbfs(pcm: bytes) -> float:
    """RMS level of little-endian mono PCM16 in dBFS (-120 for silence)."""
    count = len(pcm) // 2
    if count <= 0:
        return -120.0
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    rms = math.sqrt(sum(s * s for s in samples) / count)
    if rms <= 0:
        return -120.0
    return max(-120.0, 20.0 * math.log10(rms / 32768.0))


def build_keyterms(homework_titles=(), extra: str | None = None) -> list[str]:
    """Base vocabulary, parent extras, then today's homework titles; deduped, ≤100."""
    terms: list[str] = []

    def add(term):
        if not isinstance(term, str):
            return
        term = term.strip()
        if 1 <= len(term) <= 24 and term not in terms and len(terms) < MAX_KEYTERMS:
            terms.append(term)

    for term in BASE_KEYTERMS:
        add(term)
    # Parent-supplied extras outrank the auto-added titles when the cap hits.
    for term in (extra or "").split(","):
        add(term)
    for title in homework_titles or ():
        add(title)
    return terms


def homework_titles() -> list[str]:
    try:
        from .xiaoya_forms import get_xiaoya_forms

        items = get_xiaoya_forms().snapshot()["items"]
        return [item["title"] for item in items if isinstance(item.get("title"), str)]
    except Exception:
        return []


class TranscriptLog:
    """Opt-in (STACKCHAN_LOG_USER_TRANSCRIPTS=1) HKT-stamped transcript lines.

    Default OFF. Lines go to <dir>/transcripts-YYYY-MM-DD.log (HKT date).
    """

    def __init__(self, enabled: bool, directory: Path):
        self.enabled = enabled
        self.directory = directory

    @classmethod
    def from_env(cls) -> "TranscriptLog":
        directory = os.getenv("STACKCHAN_TRANSCRIPT_LOG_DIR") or str(
            Path.home() / "Library" / "Logs" / "xiaoya"
        )
        return cls(env_flag("STACKCHAN_LOG_USER_TRANSCRIPTS"), Path(directory))

    def write(self, who: str, text: str, ref: str | None = None, now: datetime | None = None):
        if not self.enabled or not isinstance(text, str):
            return
        now = (now or datetime.now(HKT)).astimezone(HKT)
        clean = " ".join(text.split())
        ref_part = f" reply={ref}" if ref else ""
        line = f"{now:%Y-%m-%d %H:%M:%S} HKT {who}{ref_part}: {clean}\n"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"transcripts-{now:%Y-%m-%d}.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            logger.warning("transcript log write failed")


_SENTENCE_END = re.compile(r"[。！？!?…]+|(?<![0-9])\.(?![0-9])")


def sentences_started(text: str) -> int:
    """Count sentences begun in ``text`` (a new one starts after each terminator)."""
    count = 0
    pos = 0
    for match in _SENTENCE_END.finditer(text):
        if re.search(r"\w", text[pos : match.start()]):
            count += 1
        pos = match.end()
    if re.search(r"\w", text[pos:]):
        count += 1
    return count


CANTONESE_RULES = (
    "\n\n## 對話規則（鈴鈴，香港小學功課陪伴）\n"
    "- 你係鈴鈴，陪香港小學四年級女仔娜娜做功課。佢叫娜娜；只有練英文嗰陣先叫 Elena。唔好叫佢其他名。\n"
    "- 永遠用香港廣東話（繁體字）回覆，可以夾少少英文生字。佢講廣東話夾英文係正常。"
    "永遠唔好叫佢講普通話或者國語，亦唔好話「你講國語我聽得清楚啲」。\n"
    "- 每次最多一至兩句短句，大約二十個字；最多問一條問題，而且放喺最尾。唔好問「仲有冇嘢想問？」。\n"
    "- 陪伴時保持安靜，娜娜講嘢先回應；佢靜咗就唔好自己再講。\n"
    "- 聽唔清楚就照呢個次序：第一次講「我聽唔清楚，你再講多次？」；"
    "聽到一部分就確認「你係咪話『X』？」；第二次講「你可唔可以講慢少少、近啲我？」；"
    "第三次講「不如你撳一下掣再講，或者問吓爸爸？」。\n"
    "- 同音字、新生字同數字，一定要先確認先答。聽得唔肯定嗰陣，唔好上網搜尋、唔好用任何工具，先問清楚。\n"
    "- 唔好講「你打斷咗我」；被打斷就停，唔好重講啱啱講到一半嗰句。\n"
)

TOOL_RULES = (
    "\n## 工具規則\n"
    "- 只可以用今次對話有提供嘅工具。電腦任務用原生 send_task(request)；"
    "要 relay=accepted 先可以話已經受理。冇 send_task 就即係唔可以交電腦任務。\n"
    "- 工具結果有 spoken 嘅話，只讀 spoken；唔好讀 relay、errorClass、錯誤碼、欄位名或者除錯資料。\n"
    "- show_character：只喺小朋友問生字點寫先用，只傳佢問嗰個字；照結果嘅 mode 同 spoken 回應，唔好自己加講個字點寫。\n"
)
