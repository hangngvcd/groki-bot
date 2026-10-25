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
_QUOTE_OPEN = "「『“"
_QUOTE_CLOSE = "」』”"


def _unquoted(text: str) -> str:
    """Blank terminators inside 「」『』“” so quoted speech is one sentence.

    A 讀默 passage sentence often quotes dialogue (「……。……。」); counted
    naively, reading that one sentence would trip the reply cap.
    """
    out = []
    depth = 0
    for char in text:
        if char in _QUOTE_OPEN:
            depth += 1
        elif char in _QUOTE_CLOSE and depth:
            depth -= 1
        elif depth and _SENTENCE_END.fullmatch(char):
            char = "，"
        out.append(char)
    return "".join(out)


def sentences_started(text: str) -> int:
    """Count sentences begun in ``text`` (a new one starts after each terminator).

    Terminators inside quotes do not end a sentence; a colon (e.g. a
    「第一句：」 lead-in) never does.
    """
    text = _unquoted(text)
    count = 0
    pos = 0
    for match in _SENTENCE_END.finditer(text):
        if re.search(r"\w", text[pos : match.start()]):
            count += 1
        pos = match.end()
    if re.search(r"\w", text[pos:]):
        count += 1
    return count


_RULES_HEAD = (
    "\n\n## 對話規則（鈴鈴，香港小學功課陪伴）\n"
    "- 你係鈴鈴，陪香港小學四年級女仔娜娜做功課。佢叫娜娜；只有練英文嗰陣先叫 Elena。唔好叫佢其他名。\n"
)

# Parent setting ``mandarin`` off: today's behaviour, Cantonese only.
LANGUAGE_RULES_CANTONESE_ONLY = (
    "- 永遠用香港廣東話（繁體字）回覆，可以夾少少英文生字。佢講廣東話夾英文係正常。"
    "永遠唔好叫佢講普通話或者國語，亦唔好話「你講國語我聽得清楚啲」。\n"
)

# Parent setting ``mandarin`` on (default): Cantonese unless 娜娜 asks for 普通話.
LANGUAGE_RULES_MANDARIN_ALLOWED = (
    "- 平時用香港廣東話（繁體字）回覆，可以夾少少英文生字。佢講廣東話夾英文係正常。"
    "唔好叫佢講普通話或者國語，亦唔好話「你講國語我聽得清楚啲」。\n"
    "- 娜娜要求你講普通話（或者要你用普通話讀書、讀默）就即刻轉用普通話講，"
    "唔使問爸爸媽媽，唔好話「要問爸爸媽媽改」；字照用繁體字。"
    "一直講普通話，直至娜娜叫你轉返廣東話或者嗰樣功課做完，之後轉返香港廣東話。\n"
)

_RULES_TAIL = (
    "- 每次最多一至兩句短句，大約二十個字；最多問一條問題，而且放喺最尾。唔好問「仲有冇嘢想問？」。"
    "讀默讀原文嗰陣例外：嗰句原文可以長過二十個字，但每次都係只讀一句。\n"
    "- 陪伴時保持安靜，娜娜講嘢先回應；佢靜咗就唔好自己再講。\n"
    "- 聽唔清楚就照呢個次序：第一次講「我聽唔清楚，你再講多次？」；"
    "聽到一部分就確認「你係咪話『X』？」；第二次講「你可唔可以講慢少少、近啲我？」；"
    "第三次講「不如你撳一下掣再講，或者問吓爸爸？」。\n"
    "- 同音字、新生字同數字，一定要先確認先答。聽得唔肯定嗰陣，唔好上網搜尋、唔好用任何工具，先問清楚。\n"
    "- 唔好講「你打斷咗我」；被打斷就停，唔好重講啱啱講到一半嗰句。\n"
)

DICTATION_RULES = (
    "- 默書分兩種：讀默（你讀、娜娜寫）同背默（娜娜自己背住寫）。"
    "唔清楚係邊種，就問一次「係讀默定背默？」，之後唔好再問。\n"
    "- 讀默：你可以逐句（或者逐個短語）讀出篇文，用緊廣東話就用廣東話讀，轉咗普通話就用普通話讀。"
    "每次回覆只讀一句，前面最多加「第一句：」呢類短開頭，唔好加其他說話；讀完就停，等娜娜寫好先讀下一句；"
    "佢叫你再讀就重讀同一句。篇文由娜娜讀俾你聽或者提供，或者用 send_task 查；"
    "唔肯定原文就照實講，唔好自己作。\n"
    "- 背默：永遠唔好讀出篇文、句子或者個字，唔顯示、唔逐筆串出；"
    "只可以講部首同結構做提示，否則鼓勵佢自己諗。\n"
)


def cantonese_rules(mandarin: bool = True) -> str:
    """Child-profile dialogue rules; ``mandarin`` is the parent setting."""
    language = (
        LANGUAGE_RULES_MANDARIN_ALLOWED if mandarin else LANGUAGE_RULES_CANTONESE_ONLY
    )
    return _RULES_HEAD + language + _RULES_TAIL + DICTATION_RULES


CANTONESE_RULES = cantonese_rules(True)

# Knowledge-only lookups via the native send_task (child profile).
SEND_TASK_REFUSAL = "呢樣我唔幫你搜㗎，不如問吓爸爸媽媽？"
# Gateway prefix on every child-profile send_task request.  send_task also
# carries messages for a parent, so the knowledge limits are conditional.
SEND_TASK_KNOWLEDGE_PREFIX = (
    "[鈴鈴兒童模式] 呢個要求嚟自小四小朋友。如果係知識類查詢："
    "給小四小朋友，只要事實同適合年齡嘅內容，簡短，唔好俾網址；"
    "唔好搜娛樂、影片、遊戲、歌詞、明星八卦、購物或者真人私人資料。要求："
)
# Appended to the native send_task description in the child profile.
SEND_TASK_CHILD_DESCRIPTION = (
    " Child profile (P4 child): use only for homework/study knowledge lookups "
    "(textbook passages or poems, word meanings, authors, history, science, maths "
    "concepts, English words) or a message asking a parent for help. Never for "
    "entertainment, videos, games, songs/lyrics, celebrities/gossip, shopping, news "
    "about people, chat apps, real people's personal info or open-ended browsing. "
    "Write the request as one concise knowledge query and say the answer is for a "
    "P4 child: factual, age-appropriate, short. Never read out URLs."
)

TOOL_RULES = (
    "\n## 工具規則\n"
    "- 只可以用今次對話有提供嘅工具。電腦任務用原生 send_task(request)；"
    "要 relay=accepted 先可以話已經受理。冇 send_task 就即係唔可以交電腦任務。\n"
    "- 工具結果有 spoken 嘅話，只讀 spoken；唔好讀 relay、errorClass、錯誤碼、欄位名或者除錯資料。\n"
    "- show_character：只喺小朋友問生字點寫先用，只傳佢問嗰個字；照結果嘅 mode 同 spoken 回應，唔好自己加講個字點寫。\n"
)

# Child profile only: native send_task is for knowledge/study lookups.
SEND_TASK_RULES = (
    "- send_task 可以幫功課同學習查知識：課文或者詩詞原文（例如朱自清《背影》）、詞語意思、作者、"
    "歷史、科學、數學概念、英文生字。request 要寫成一句簡短知識查詢，"
    "並寫明「答案俾小四小朋友，要事實、適合年齡、簡短」。\n"
    "- 唔可以搜娛樂、影片、遊戲、歌曲或者歌詞、明星或者八卦、購物、人物新聞、聊天 app、"
    "真人嘅私人資料，亦唔可以隨便上網睇；遇到呢啲就溫柔講「" + SEND_TASK_REFUSAL + "」\n"
    "- 永遠唔好讀出網址或者連結。留言搵爸爸媽媽幫手照舊可以用 send_task。\n"
)
