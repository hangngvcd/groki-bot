# SPDX-License-Identifier: MIT
"""Build 19 dialogue fixes: half-duplex barge-in, cancel guard, reply cap,
transcription config, Cantonese rules and opt-in transcript logging."""

import asyncio
import base64
import logging
import struct
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp import gbot_dialogue
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge


def bridge():
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
    )
    b._send = AsyncMock()
    return b


def sent_types(b):
    return [call.args[0]["type"] for call in b._send.await_args_list]


def tone(ms, amplitude=8000):
    samples = int(16 * ms)
    return struct.pack("<%dh" % samples, *([amplitude, -amplitude] * (samples // 2)))


def silence(ms):
    return b"\x00\x00" * int(16 * ms)


def audio_delta(rid):
    return {
        "type": "response.output_audio.delta",
        "response_id": rid,
        "delta": base64.b64encode(b"\x00\x00" * 240).decode(),
    }


@pytest.mark.asyncio
async def test_speech_started_ignored_once_tts_started():
    b = bridge()
    b.playback_active = lambda: True
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event({"type": "input_audio_buffer.speech_started"})
    await b.send_audio(tone(1500))
    b._on_interrupt.assert_not_awaited()
    assert "response.cancel" not in sent_types(b)
    assert b.metrics["speech_starts_ignored_playback"] == 1
    await b._handle_event(audio_delta("r"))
    assert b._on_audio.await_count == 1


@pytest.mark.asyncio
async def test_device_abort_still_interrupts_during_playback():
    b = bridge()
    b.playback_active = lambda: True
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b.interrupt()
    b._on_interrupt.assert_awaited_once()
    assert sent_types(b).count("response.cancel") == 1
    assert b.metrics["interrupt_reasons"] == {"device_abort": 1}


@pytest.mark.asyncio
async def test_short_noise_before_audio_does_not_interrupt():
    b = bridge()
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event({"type": "input_audio_buffer.speech_started"})
    await b.send_audio(tone(300))
    await b._handle_event({"type": "input_audio_buffer.speech_stopped"})
    await b.send_audio(tone(600))  # after stop: no longer a barge candidate
    b._on_interrupt.assert_not_awaited()
    assert b.metrics["speech_starts_ignored_short"] == 1


@pytest.mark.asyncio
async def test_quiet_speech_before_audio_does_not_interrupt():
    b = bridge()
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event({"type": "input_audio_buffer.speech_started"})
    await b.send_audio(tone(1000, amplitude=100))  # about -50 dBFS
    b._on_interrupt.assert_not_awaited()


@pytest.mark.asyncio
async def test_sustained_speech_before_audio_interrupts():
    b = bridge()
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event({"type": "input_audio_buffer.speech_started"})
    for _ in range(5):
        await b.send_audio(tone(100))
    b._on_interrupt.assert_not_awaited()
    await b.send_audio(tone(120))
    b._on_interrupt.assert_awaited_once()
    assert b.metrics["interrupt_reasons"] == {"sustained_speech": 1}
    assert sent_types(b).count("response.cancel") == 1


@pytest.mark.asyncio
async def test_no_cancel_after_server_finished_response():
    b = bridge()
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event(
        {"type": "response.done", "response": {"id": "r", "status": "cancelled"}}
    )
    await b.interrupt()
    assert "response.cancel" not in sent_types(b)
    assert b.metrics["response_status"] == {"cancelled": 1}
    # A stale in-progress flag for an already-done id is still guarded.
    b._response_in_progress = True
    b._active_response = "r"
    await b.interrupt()
    assert "response.cancel" not in sent_types(b)
    assert b.metrics["cancels_skipped"] == 1


@pytest.mark.asyncio
async def test_no_response_create_while_vad_response_pending():
    b = bridge()
    b._pending_response = True
    await b._handle_event({"type": "input_audio_buffer.committed"})
    await b._flush_pending()
    assert "response.create" not in sent_types(b)
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    assert not b._vad_commit_pending
    await b.stop()


@pytest.mark.asyncio
async def test_reply_capped_after_second_sentence():
    b = bridge()
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    for delta in ("好呀。", "你想先做", "中文？", "我哋"):
        await b._handle_event(
            {"type": "response.output_audio_transcript.delta", "response_id": "r", "delta": delta}
        )
    assert sent_types(b).count("response.cancel") == 1
    assert b.metrics["capped_replies"] == 1
    await b._handle_event(audio_delta("r"))
    b._on_audio.assert_not_awaited()
    await b._handle_event(
        {"type": "response.done", "response": {"id": "r", "status": "cancelled"}}
    )
    await asyncio.sleep(0)
    b._on_turn_complete.assert_awaited_once()  # capped reply still ends normally


@pytest.mark.asyncio
async def test_two_sentence_reply_not_capped():
    b = bridge()
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event(
        {"type": "response.output_audio_transcript.delta", "response_id": "r", "delta": "好呀。你想先做中文？"}
    )
    assert "response.cancel" not in sent_types(b)


def test_sentence_counter():
    assert gbot_dialogue.sentences_started("你好。我係鈴鈴！今日") == 3
    assert gbot_dialogue.sentences_started("3.5 加 2 係 5.5。") == 1
    assert gbot_dialogue.sentences_started("") == 0


def test_transcription_config_and_keyterms():
    assert gbot_dialogue.transcription_config("auto", []) == {"model": "grok-transcribe"}
    cfg = gbot_dialogue.transcription_config("yue", ["鈴鈴"])
    assert cfg == {"model": "grok-transcribe", "language_hint": "yue", "keyterms": ["鈴鈴"]}
    terms = gbot_dialogue.build_keyterms(
        ["中文抄生字", "中文抄生字", "x" * 40] + [f"功課{i}" for i in range(200)], "Elena, 蝴蝶"
    )
    assert len(terms) <= 100
    assert len(terms) == len(set(terms))
    for term in ("鈴鈴", "娜娜", "Elena", "中文抄生字", "蝴蝶"):
        assert term in terms
    assert "x" * 40 not in terms


def test_rules_are_cantonese_and_keep_tool_meaning():
    rules = gbot_dialogue.CANTONESE_RULES
    for phrase in ("娜娜", "Elena", "香港廣東話", "普通話", "我聽唔清楚，你再講多次？",
                   "你係咪話『X』？", "你可唔可以講慢少少、近啲我？",
                   "不如你撳一下掣再講，或者問吓爸爸？", "唔好上網搜尋"):
        assert phrase in rules
    assert "仲有冇嘢想問" in rules  # only as a prohibition
    tools = gbot_dialogue.TOOL_RULES
    for phrase in ("send_task", "relay=accepted", "spoken", "show_character"):
        assert phrase in tools
    # Traditional only: no simplified forms from the old English/简体 tail.
    for simplified in ("结果", "错误", "调试", "请"):
        assert simplified not in rules + tools


def test_transcript_log_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("STACKCHAN_LOG_USER_TRANSCRIPTS", raising=False)
    monkeypatch.setenv("STACKCHAN_TRANSCRIPT_LOG_DIR", str(tmp_path))
    log = gbot_dialogue.TranscriptLog.from_env()
    log.write("娜娜", "你好")
    assert not log.enabled
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_transcript_log_writes_final_hkt_lines(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_LOG_USER_TRANSCRIPTS", "1")
    monkeypatch.setenv("STACKCHAN_TRANSCRIPT_LOG_DIR", str(tmp_path))
    b = bridge()
    for text in ("鈴鈴，蝴蝶", "鈴鈴，蝴蝶個蝶字點寫呀"):
        await b._handle_event({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "u1", "transcript": text,
        })
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event({
        "type": "response.output_audio_transcript.done", "response_id": "r",
        "transcript": "你係咪話「蝶」？",
    })
    await b.stop()
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].name.startswith("transcripts-")
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert " HKT 娜娜: 鈴鈴，蝴蝶個蝶字點寫呀" in lines[0]
    assert " HKT 鈴鈴 reply=r: 你係咪話「蝶」？" in lines[1]


def test_transcript_log_uses_hkt_date(tmp_path):
    from datetime import timezone

    log = gbot_dialogue.TranscriptLog(True, tmp_path)
    log.write("娜娜", "hi", now=datetime(2026, 10, 24, 17, 30, tzinfo=timezone.utc))
    assert (tmp_path / "transcripts-2026-10-25.log").read_text().startswith(
        "2026-10-25 01:30:00 HKT"
    )


@pytest.mark.asyncio
async def test_error_log_has_message_without_secrets(caplog):
    b = bridge()
    caplog.set_level(logging.INFO)
    await b._handle_event({
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "response_cancel_not_active",
            "message": "Cancellation failed: no active response. token=abc123secret",
        },
    })
    text = caplog.text
    assert "no active response" in text
    assert "abc123secret" not in text
    assert b.metrics["errors"] == 1
    await b.stop()
    assert "session stats" in caplog.text and "errors=1" in caplog.text


@pytest.mark.asyncio
async def test_session_config_vad_and_transcription(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.delenv("STACKCHAN_GBOT_TRANSCRIBE_LANGUAGE", raising=False)
    monkeypatch.setenv("STACKCHAN_GBOT_VAD_SILENCE_MS", "1300")
    b = bridge()
    assert b.transcribe_language == "yue"
    assert b.vad_silence_ms == 1300 and b.vad_prefix_ms == 300
    assert b.barge_min_ms == 600 and b.barge_dbfs == -40.0
    assert b.max_sentences == 2
