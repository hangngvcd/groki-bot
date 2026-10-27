# SPDX-License-Identifier: MIT
"""Offline 部首/結構 lookup + show_character wiring (ticket 36)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp import char_structure, gbot_dialogue, xiaoya_forms
from stackchan_mcp.char_structure import lookup_char_structure, reload_char_structure
from stackchan_mcp.voice_tools import VoiceToolExecutor


def test_hu_is_left_right_with_three_dots_water():
    hit = lookup_char_structure("湖")
    assert hit["structure"] == "左右"
    assert "三點水" in hit["radical"]


@pytest.mark.parametrize(
    ("ch", "structure"),
    [
        ("湖", "左右"),
        ("思", "上下"),
        ("一", "單一"),
    ],
)
def test_known_structures(ch, structure):
    hit = lookup_char_structure(ch)
    assert hit.get("structure") == structure


def test_unknown_char_returns_empty(monkeypatch, tmp_path):
    empty = {
        "version": 1,
        "structures": ["左右", "上下", "左中右", "上中下", "半包圍", "全包圍", "品字", "交疊", "單一"],
        "radicals": {"氵": "三點水"},
        "entries": {},
    }
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(empty), encoding="utf-8")
    monkeypatch.setenv("STACKCHAN_CHAR_STRUCTURE_PATH", str(path))
    reload_char_structure()
    try:
        assert lookup_char_structure("湖") == {}
        assert lookup_char_structure("") == {}
        assert lookup_char_structure("ab") == {}
    finally:
        monkeypatch.delenv("STACKCHAN_CHAR_STRUCTURE_PATH", raising=False)
        reload_char_structure()


@pytest.mark.asyncio
async def test_dictation_returns_hu_hints(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    forms = xiaoya_forms.XiaoyaForms(tmp_path / "forms.json")
    forms.add_items(["中文默書"], source="parent")
    monkeypatch.setattr(xiaoya_forms, "_forms", forms)
    sender = AsyncMock(return_value=True)
    forms.register_sender(sender, push=False)
    forms._session_ready = True
    executor = VoiceToolExecutor()
    try:
        result = await executor.execute("show_character", {"character": "湖"})
        assert result["ok"] is True
        assert result["mode"] == "dictation" and result["shown"] is False
        assert result["structure"] == "左右"
        assert "三點水" in result["radical"]
        assert "never invent" in result["note"]
        assert "Speak ONLY" in result["note"] or "ONLY" in result["note"]
        assert "提示" in result["spoken"]
        assert "部首同結構" not in result["spoken"]  # don't promise invention
        sender.assert_not_awaited()
    finally:
        await executor.close()
        forms.unregister_sender()


@pytest.mark.asyncio
async def test_dictation_missing_lookup_encourage_only(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    empty = {
        "version": 1,
        "structures": ["左右", "上下", "左中右", "上中下", "半包圍", "全包圍", "品字", "交疊", "單一"],
        "radicals": {},
        "entries": {},
    }
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(empty), encoding="utf-8")
    monkeypatch.setenv("STACKCHAN_CHAR_STRUCTURE_PATH", str(path))
    reload_char_structure()
    forms = xiaoya_forms.XiaoyaForms(tmp_path / "forms.json")
    forms.add_items(["中文默書"], source="parent")
    monkeypatch.setattr(xiaoya_forms, "_forms", forms)
    executor = VoiceToolExecutor()
    try:
        result = await executor.execute("show_character", {"character": "湖"})
        assert result["mode"] == "dictation"
        assert "radical" not in result and "structure" not in result
        assert "你自己諗吓" in result["spoken"]
        assert "部首" not in result["spoken"] and "提示" not in result["spoken"]
        assert "never invent" in result["note"]
        assert "No radical/structure" in result["note"]
    finally:
        await executor.close()
        monkeypatch.delenv("STACKCHAN_CHAR_STRUCTURE_PATH", raising=False)
        reload_char_structure()


@pytest.mark.asyncio
async def test_copy_note_forbids_structure(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    forms = xiaoya_forms.XiaoyaForms(tmp_path / "forms.json")
    forms.add_items(["中文抄生字"], source="parent")
    monkeypatch.setattr(xiaoya_forms, "_forms", forms)
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(forms, "send_device", sender)
    # Avoid needing fonts/device: stub render
    from stackchan_mcp import xiaoya_glyph

    monkeypatch.setattr(xiaoya_glyph, "render_glyph_png", lambda _c: b"\x89PNG")
    monkeypatch.setattr(
        xiaoya_glyph,
        "glyph_message",
        lambda png, hold_ms=None: {"cmd": "glyph", "png": "xx", "hold_ms": 10000},
    )
    executor = VoiceToolExecutor()
    try:
        result = await executor.execute("show_character", {"character": "湖"})
        assert result["mode"] == "copy" and result["shown"] is True
        assert "radical" not in result and "structure" not in result
        assert "do NOT add 部首" in result["note"] or "NOT add" in result["note"]
        assert result["spoken"] == "睇清楚個字，收起咗之後自己喺簿度寫一次。"
        sender.assert_awaited()
    finally:
        await executor.close()


def test_dialogue_rules_forbid_invention():
    assert "唔准自己估" in gbot_dialogue.DICTATION_RULES
    assert "radical/structure" in gbot_dialogue.DICTATION_RULES
    assert "有顯示" in gbot_dialogue.TOOL_RULES
    assert "唔准自己估" in gbot_dialogue.TOOL_RULES
