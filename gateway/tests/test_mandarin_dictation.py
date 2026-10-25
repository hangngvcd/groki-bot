# SPDX-License-Identifier: MIT
"""Ticket 22: parent setting ``mandarin``, 讀默/背默 rules, send_task limits."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp import gbot_dialogue
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge
from stackchan_mcp.parent_cli import cli_value
from stackchan_mcp.parent_settings import (
    SCHEMA,
    ParentSettings,
    device_support,
    get_parent_settings,
    mandarin_enabled,
    validate,
)


# --- schema ---------------------------------------------------------------


def test_mandarin_schema_default_on(tmp_path):
    assert SCHEMA["mandarin"] == {
        "type": "boolean",
        "default": True,
        "description_zh": "容許鈴鈴應娜娜要求講普通話",
    }
    store = ParentSettings(tmp_path / "s.json")
    assert store.get()["mandarin"] is True
    store.set("mandarin", False, source="test")
    assert ParentSettings(tmp_path / "s.json").get()["mandarin"] is False
    with pytest.raises(ValueError):
        validate({"mandarin": "off"})
    with pytest.raises(ValueError):
        validate({"mandarin": 1})


@pytest.mark.parametrize(
    "value,expected",
    [("on", True), ("OFF", False), ("true", True), ("false", False), ("1", True), ("0", False)],
)
def test_cli_parses_mandarin_boolean(value, expected):
    assert cli_value("mandarin", value) is expected


def test_cli_rejects_bad_mandarin_value():
    with pytest.raises(ValueError):
        cli_value("mandarin", "maybe")


def test_device_support_marks_mandarin_as_gateway_applied():
    support = device_support()["mandarin"]
    assert support["applied_in_gateway"] is True
    assert support["applied_to_device"] is False


def test_mandarin_enabled_reads_store():
    assert mandarin_enabled() is True
    get_parent_settings().set("mandarin", False, source="test")
    assert mandarin_enabled() is False


# --- dialogue rules -------------------------------------------------------


def test_rules_mandarin_on_allow_switch_without_parents():
    rules = gbot_dialogue.cantonese_rules(True)
    assert rules == gbot_dialogue.CANTONESE_RULES
    assert "永遠唔好叫佢講普通話" not in rules
    assert "平時用香港廣東話（繁體字）回覆" in rules
    assert "即刻轉用普通話講" in rules
    assert "唔使問爸爸媽媽，唔好話「要問爸爸媽媽改」" in rules
    assert "字照用繁體字" in rules
    assert "直至娜娜叫你轉返廣東話或者嗰樣功課做完" in rules
    # Still never asks HER to speak Mandarin to be heard better.
    assert "唔好叫佢講普通話或者國語" in rules
    assert "你講國語我聽得清楚啲" in rules


def test_rules_mandarin_off_keep_cantonese_only():
    rules = gbot_dialogue.cantonese_rules(False)
    assert "永遠用香港廣東話（繁體字）回覆" in rules
    assert "永遠唔好叫佢講普通話或者國語" in rules
    assert "即刻轉用普通話講" not in rules


@pytest.mark.parametrize("mandarin", [True, False])
def test_dictation_rules_distinguish_modes(mandarin):
    rules = gbot_dialogue.cantonese_rules(mandarin)
    assert gbot_dialogue.DICTATION_RULES in rules
    assert "係讀默定背默？" in rules
    assert "讀默：你可以逐句（或者逐個短語）讀出篇文" in rules
    assert "轉咗普通話就用普通話讀" in rules
    assert "佢叫你再讀就重讀同一句" in rules
    assert "背默：永遠唔好讀出篇文、句子或者個字" in rules
    assert "部首同結構" in rules
    # The short-reply rule stays, with only the 讀默 sentence excepted.
    assert "每次最多一至兩句短句" in rules
    assert "讀默讀原文嗰陣例外" in rules


def test_send_task_rules_are_knowledge_only():
    rules = gbot_dialogue.SEND_TASK_RULES
    for phrase in ("朱自清《背影》", "詞語意思", "英文生字", "小四小朋友",
                   "影片", "遊戲", "歌詞", "八卦", "購物", "私人資料",
                   "呢樣我唔幫你搜㗎，不如問吓爸爸媽媽？", "唔好讀出網址"):
        assert phrase in rules
    assert "send_task" in gbot_dialogue.TOOL_RULES
    assert "私人資料" not in gbot_dialogue.TOOL_RULES  # child-only block


# --- reply cap ------------------------------------------------------------


def test_sentence_counter_ignores_terminators_inside_quotes():
    passage = "第一句：「我買幾個橘子去。你就在此地，不要走動。」"
    assert gbot_dialogue.sentences_started(passage) == 1
    assert gbot_dialogue.sentences_started("好。" + passage) == 2
    assert gbot_dialogue.sentences_started("你好。我係鈴鈴！今日") == 3


def _bridge():
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
    )
    b._send = AsyncMock()
    return b


@pytest.mark.asyncio
async def test_long_dictation_sentence_is_not_capped():
    b = _bridge()
    assert b.max_sentences == 2
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    for delta in ("第二句：我說道：「爸爸，你走吧。」", "他望車外看了看，說：「我買幾個橘子去。你就在此地，不要走動。」"):
        await b._handle_event(
            {"type": "response.output_audio_transcript.delta", "response_id": "r", "delta": delta}
        )
    types = [call.args[0]["type"] for call in b._send.await_args_list]
    assert "response.cancel" not in types


@pytest.mark.asyncio
async def test_three_plain_sentences_still_capped():
    b = _bridge()
    await b._handle_event({"type": "response.created", "response": {"id": "r"}})
    await b._handle_event(
        {"type": "response.output_audio_transcript.delta", "response_id": "r",
         "delta": "好呀。我哋做中文。之後做數學"}
    )
    assert b.metrics["capped_replies"] == 1


# --- prompts --------------------------------------------------------------


def _executor_instructions(monkeypatch):
    from stackchan_mcp.voice_tools import VoiceToolExecutor

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setenv("STACKCHAN_MAC_CONTROL", "0")
    monkeypatch.setenv("STACKCHAN_ASK_CLAUDE", "0")
    return VoiceToolExecutor(SimpleNamespace(device_connected=False)).instructions()


def test_child_instructions_mandarin_on(monkeypatch):
    text = _executor_instructions(monkeypatch)
    assert "講普通話唔係要改設定" in text
    assert "呢樣要問媽媽或者爸爸幫你改㗎。" in text  # other settings unchanged
    assert "係讀默定背默？" in text
    assert "背默就唔好講出個字" in text
    assert "讀默就可以重讀原文嗰句" in text


def test_child_instructions_mandarin_off(monkeypatch):
    get_parent_settings().set("mandarin", False, source="test")
    text = _executor_instructions(monkeypatch)
    assert "講普通話唔係要改設定" not in text
    assert "係讀默定背默？" in text


# --- live settings change -------------------------------------------------


def _bare_bridge():
    bridge = GbotVoiceBridge.__new__(GbotVoiceBridge)
    bridge._stopped = False
    bridge.running = True
    bridge._tools = set()
    bridge._send = AsyncMock()
    bridge._parent_settings = None
    return bridge


@pytest.mark.asyncio
async def test_settings_change_injects_note_without_response(caplog):
    caplog.set_level(logging.INFO, logger="stackchan_mcp.gbot_voice_bridge")
    bridge = _bare_bridge()
    bridge._watch_settings()
    settings = get_parent_settings()
    settings.set("volume", 30, source="test")  # unrelated key: no note
    await asyncio.sleep(0)
    assert not bridge._send.await_args_list
    settings.set("mandarin", False, source="test")
    await asyncio.gather(*list(bridge._tools))
    settings.set("mandarin", True, source="test")
    await asyncio.gather(*list(bridge._tools))
    sent = [call.args[0] for call in bridge._send.await_args_list]
    assert [e["type"] for e in sent] == ["conversation.item.create"] * 2
    texts = [e["item"]["content"][0]["text"] for e in sent]
    assert all(e["item"]["role"] == "user" for e in sent)
    assert texts[0] == GbotVoiceBridge.MANDARIN_OFF_NOTE
    assert texts[1] == GbotVoiceBridge.MANDARIN_ON_NOTE
    assert all(t.startswith("[系統通知，唔使回應]") for t in texts)
    assert "context noted: settings mandarin" in caplog.text
    # After stop the listener is gone.
    bridge._homework_forms = None
    bridge._parent_settings.remove_listener(bridge._on_settings_changed)
    settings.set("mandarin", False, source="test")
    await asyncio.sleep(0)
    assert len(bridge._send.await_args_list) == 2


# --- send_task in the child profile ---------------------------------------


def _desktop_bridge(monkeypatch):
    monkeypatch.setenv("STACKCHAN_TOOL_BOT", "desktop")
    monkeypatch.setenv("STACKCHAN_TOOL_BOT_ID", "agent")
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
        agent_id="agent",
        tool_executor=SimpleNamespace(
            declarations=lambda: [],
            instructions=lambda **kwargs: "",
            close=AsyncMock(),
            observe_user_input=AsyncMock(),
        ),
    )
    b._send = AsyncMock()
    b._native = SimpleNamespace(ready=True, close=AsyncMock())
    b.running = True
    b._configure_tools(
        {
            "tools": [
                {
                    "type": "function",
                    "name": "send_task",
                    "description": "Native",
                    "parameters": {
                        "type": "object",
                        "properties": {"request": {"type": "string"}},
                        "required": ["request"],
                    },
                }
            ]
        }
    )
    return b


@pytest.mark.parametrize("child", ["1", "0"])
@pytest.mark.asyncio
async def test_send_task_prefix_and_description_child_only(monkeypatch, child):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", child)
    b = _desktop_bridge(monkeypatch)
    description = b._remote_tools["send_task"]["description"]
    b._local = AsyncMock(return_value={"receipt": "accepted"})
    await b._handle_event({"type": "input_audio_buffer.speech_started"})
    await b._tool(
        {"name": "send_task", "call_id": "one", "arguments": '{"request":"朱自清《背影》原文"}'}
    )
    request = b._local.call_args.args[1]["input"]["request"]
    if child == "1":
        assert description.startswith("Native") and "P4 child" in description
        assert request == gbot_dialogue.SEND_TASK_KNOWLEDGE_PREFIX + "朱自清《背影》原文"
        assert "唔好俾網址" in request
        assert "知識類查詢" in request
        assert gbot_dialogue.SEND_TASK_RULES in b._provider_tool_instructions()
    else:
        assert description == "Native"
        assert request == "朱自清《背影》原文"
    await b.stop()
