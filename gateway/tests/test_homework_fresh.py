# SPDX-License-Identifier: MIT
"""A parent edits the homework list mid-session; the model must not reuse a
stale show_character / homework_list answer (08:36 incident)."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp import gemini_voice_proxy, xiaoya_forms
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge
from stackchan_mcp.voice_tools import (
    SHOW_CHARACTER_FRESH_NOTE,
    ToolMetadata,
    VoiceToolExecutor,
)
from stackchan_mcp.xiaoya_forms import XiaoyaForms


@pytest.fixture
def forms(tmp_path, monkeypatch):
    f = XiaoyaForms(path=tmp_path / "forms.json", today=lambda: "2026-10-25", volume_source=lambda: 50)
    monkeypatch.setattr(xiaoya_forms, "get_xiaoya_forms", lambda: f)
    return f


def test_forms_notify_listeners_on_every_list_change(forms):
    seen = []
    forms.add_change_listener(lambda action, source: seen.append((action, source)))
    forms.add_items(["中文抄生字"], source="parent")
    forms.redo_items([1], source="parent")
    forms.mark_done(1, source="child-voice")
    forms.remove_items([1], source="parent")
    assert seen == [
        ("add", "parent"),
        ("redo", "parent"),
        ("done", "child-voice"),
        ("remove", "parent"),
    ]


def test_forms_listener_failure_does_not_break_the_edit(forms):
    def boom(action, source):
        raise RuntimeError("listener")

    forms.add_change_listener(boom)
    result = forms.add_items(["默書"], source="parent")
    assert result["total"] == 1
    forms.remove_change_listener(boom)
    forms.remove_change_listener(boom)  # idempotent


@pytest.mark.asyncio
async def test_dictation_result_tells_model_to_call_again(forms):
    result = await VoiceToolExecutor._dispatch_show_character({"character": "湖"})
    assert result["mode"] == "dictation" and result["shown"] is False
    assert SHOW_CHARACTER_FRESH_NOTE in result["note"]
    assert "call show_character" in result["note"]
    listed = VoiceToolExecutor._dispatch_homework("homework_list", {})
    assert listed["items"] == [] and "homework_list again" in listed["note"]


def _bare_bridge():
    bridge = GbotVoiceBridge.__new__(GbotVoiceBridge)
    bridge._stopped = False
    bridge.running = True
    bridge._tools = set()
    bridge._send = AsyncMock()
    return bridge


@pytest.mark.asyncio
async def test_parent_change_injects_context_note_without_a_response(forms, caplog):
    bridge = _bare_bridge()
    bridge._watch_homework()
    caplog.set_level(logging.INFO, logger="stackchan_mcp.gbot_voice_bridge")
    forms.add_items(["中文抄生字"], source="parent")
    await asyncio.gather(*list(bridge._tools))
    sent = [call.args[0] for call in bridge._send.await_args_list]
    assert len(sent) == 1
    item = sent[0]
    assert item["type"] == "conversation.item.create"
    assert item["item"]["role"] == "user"
    assert "show_character" in item["item"]["content"][0]["text"]
    assert not [e for e in sent if e.get("type") == "response.create"]
    assert "homework change noted action=add" in caplog.text

    # The child's own mark_done is already in context: no note.
    forms.mark_done(1, source="child-voice")
    await asyncio.sleep(0)
    assert len(bridge._send.await_args_list) == 1

    # After the bridge stops listening, edits reach nobody.
    forms.remove_change_listener(bridge._on_homework_changed)
    forms.remove_items([1], source="parent")
    await asyncio.sleep(0)
    assert len(bridge._send.await_args_list) == 1


@pytest.mark.asyncio
async def test_stopped_bridge_ignores_change(forms):
    bridge = _bare_bridge()
    bridge._stopped = True
    bridge._on_homework_changed("add", "parent")
    assert not bridge._tools
    bridge._send.assert_not_awaited()


@pytest.mark.asyncio
async def test_show_character_timeout_is_logged_and_retryable(caplog):
    async def slow(name, args):
        await asyncio.sleep(1)
        return {"ok": True}

    fake = SimpleNamespace(
        _tool_metadata=lambda name: ToolMetadata(True, "glyph", 0.01),
        _dispatch_tool=slow,
    )
    caplog.set_level(logging.WARNING, logger="stackchan_mcp.voice_tools")
    result = await VoiceToolExecutor._dispatch_tool_with_timeout(fake, "show_character", {})
    assert result["ok"] is False and "timeout" in result["error"]
    assert "show_character again" in result["note"]
    assert "name=show_character timed out" in caplog.text


@pytest.mark.asyncio
async def test_glyph_prewarm_runs_once_off_the_loop(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini_voice_proxy, "_glyph_prewarm_task", None)
    monkeypatch.delenv("STACKCHAN_GLYPH_PREWARM", raising=False)
    import stackchan_mcp.voice_tools as vt

    monkeypatch.setattr(vt, "prewarm_glyph_renderer", lambda: calls.append(1))
    gemini_voice_proxy._schedule_glyph_prewarm()
    gemini_voice_proxy._schedule_glyph_prewarm()
    await gemini_voice_proxy._glyph_prewarm_task
    assert calls == [1]
