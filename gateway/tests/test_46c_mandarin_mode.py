"""46c: Mandarin mode replaces the Cantonese rules, sets the ASR hint, notes the
switch in the conversation, and makes tool spoken text Mandarin."""
import datetime as dt
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp import gbot_dialogue, voice_tools
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge


@pytest.fixture(autouse=True)
def _reset_mode():
    gbot_dialogue.set_active_language_mode("yue")
    yield
    gbot_dialogue.set_active_language_mode("yue")


def _bridge(monkeypatch):
    from stackchan_mcp import gbot_voice_bridge as module

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setattr(module, "mandarin_enabled", lambda: True)
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
    )
    head = "persona\n\ntools"
    b._instructions_head = head
    b._child_rules = True
    b._base_instructions = head + gbot_dialogue.cantonese_rules(True)
    b.transcribe_language = "yue"
    b._audio_config = {
        "input": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "transcription": gbot_dialogue.transcription_config("yue", ["默書"]),
        },
        "output": {"format": {"type": "audio/pcm", "rate": 24000}},
    }
    b._send = AsyncMock()
    return b


async def _say(b, text, item):
    await b._handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": text,
            "item_id": item,
        }
    )


def test_mandarin_mode_rules_replace_cantonese_language_rules():
    rules = gbot_dialogue.cantonese_rules(True, "cmn")
    assert gbot_dialogue.LANGUAGE_RULES_MANDARIN_MODE in rules
    assert gbot_dialogue.LANGUAGE_RULES_MANDARIN_ALLOWED not in rules
    assert "平時用香港廣東話" not in rules
    # Parent setting off never yields Mandarin rules.
    assert gbot_dialogue.cantonese_rules(False, "cmn") == gbot_dialogue.cantonese_rules(False)
    assert gbot_dialogue.language_mode_header("yue") == ""


@pytest.mark.asyncio
async def test_switch_sends_mandarin_prompt_hint_and_note(monkeypatch):
    b = _bridge(monkeypatch)
    await _say(b, "可以說普通話嗎？", "a")
    sent = [c.args[0] for c in b._send.await_args_list]
    update = next(m for m in sent if m["type"] == "session.update")
    instr = update["session"]["instructions"]
    assert instr.startswith(gbot_dialogue.language_mode_header("cmn"))
    assert instr.endswith(gbot_dialogue.language_mode_block("cmn"))
    assert "平時用香港廣東話" not in instr
    assert "persona\n\ntools" in instr
    audio = update["session"]["audio"]
    assert audio["input"]["transcription"]["language_hint"] == "zh"
    assert audio["input"]["transcription"]["keyterms"] == ["默書"]
    assert audio["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    notes = [m for m in sent if m["type"] == "conversation.item.create"]
    assert notes and notes[0]["item"]["content"][0]["text"] == gbot_dialogue.MANDARIN_SWITCH_NOTE
    assert "response.create" not in [m["type"] for m in sent]
    assert gbot_dialogue.active_language_mode() == "cmn"
    # The stored config is not mutated by the per-mode copy.
    assert b._audio_config["input"]["transcription"]["language_hint"] == "yue"

    await _say(b, "講返廣東話", "b")
    sent = [c.args[0] for c in b._send.await_args_list]
    back = [m for m in sent if m["type"] == "session.update"][-1]
    assert back["session"]["instructions"] == b._base_instructions + gbot_dialogue.language_mode_block("yue")
    assert back["session"]["audio"]["input"]["transcription"]["language_hint"] == "yue"
    assert sent[-1]["item"]["content"][0]["text"] == gbot_dialogue.CANTONESE_SWITCH_NOTE
    assert gbot_dialogue.active_language_mode() == "yue"


def _frozen(monkeypatch, hour, minute):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 31, hour, minute, tzinfo=tz)

    monkeypatch.setattr(dt, "datetime", Frozen)


@pytest.mark.parametrize(
    "hour,minute,tail",
    [
        (2, 23, "凌晨兩點二十三分。"),
        (9, 5, "上午九點零五分。"),
        (12, 0, "中午十二點整。"),
        (14, 30, "下午兩點三十分。"),
        (20, 1, "晚上八點零一分。"),
    ],
)
def test_datetime_spoken_follows_language_mode(monkeypatch, hour, minute, tail):
    _frozen(monkeypatch, hour, minute)
    gbot_dialogue.set_active_language_mode("cmn")
    result = voice_tools.VoiceToolExecutor._dispatch_get_datetime()
    assert result["spoken_zh"] == "現在是香港時間十月三十一日星期六，" + tail
    gbot_dialogue.set_active_language_mode("yue")
    result = voice_tools.VoiceToolExecutor._dispatch_get_datetime()
    assert result["spoken_zh"].startswith("而家係香港時間十月三十一號星期六，")


def test_battery_spoken_follows_language_mode(monkeypatch):
    from stackchan_mcp.debug_status import DebugStatus

    status = DebugStatus(clock=lambda: 1000)
    status.device_connected_flag = True
    monkeypatch.setattr(voice_tools, "get_debug_status", lambda: status)
    gbot_dialogue.set_active_language_mode("cmn")
    assert voice_tools.VoiceToolExecutor._dispatch_battery_status()["spoken"].startswith("我現在讀不到")
    status.device_battery_percent = 15
    status.device_battery_at = 1000
    spoken = voice_tools.VoiceToolExecutor._dispatch_battery_status()["spoken"]
    assert spoken.startswith("我還有大約15%的電。") and "充電" in spoken
    gbot_dialogue.set_active_language_mode("yue")
    assert voice_tools.VoiceToolExecutor._dispatch_battery_status()["spoken"].startswith("我仲有大約15%電。")


def test_unknown_battery_without_reason_does_not_claim_usb(monkeypatch):
    from stackchan_mcp.debug_status import DebugStatus

    status = DebugStatus(clock=lambda: 1000)
    status.device_connected_flag = True
    monkeypatch.setattr(voice_tools, "get_debug_status", lambda: status)
    assert "插住電" not in voice_tools.VoiceToolExecutor._dispatch_battery_status()["spoken"]
