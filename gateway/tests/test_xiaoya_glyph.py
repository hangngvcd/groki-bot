# SPDX-License-Identifier: MIT
import asyncio
import base64
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest
import websockets

from stackchan_mcp import xiaoya_forms
from stackchan_mcp.esp32_client import ESP32Manager
from stackchan_mcp.voice_tools import VoiceToolExecutor
from stackchan_mcp.xiaoya_glyph import (
    clean_characters,
    font_name_for,
    glyph_message,
    render_glyph_png,
    vocab_mode,
)


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([{"title": "默書", "done": False}], "dictation"),
        ([{"title": "抄寫", "done": False}], "copy"),
        (
            [{"title": "默書", "done": False}, {"title": "抄寫", "done": False}],
            "dictation",
        ),
        ([{"title": "默書", "done": True}, {"title": "抄寫", "done": False}], "copy"),
        ([{"title": "抄寫", "done": True}], "dictation"),
        ([], "dictation"),
        ([{"title": "數學", "done": False}], "dictation"),
        (None, "dictation"),
        ([None], "dictation"),
        ([{"title": 1, "done": False}], "dictation"),
        ([{}], "dictation"),
    ],
)
def test_mode(items, expected):
    assert vocab_mode(items) == expected


@pytest.mark.parametrize("text", ["蝶", "蝴蝶", "〇", "\U00031350", "「 蝴，蝶。」"])
def test_clean(text):
    assert 1 <= len(clean_characters(text)) <= 4
    assert clean_characters("「 蝴，蝶。」") == "蝴蝶"


@pytest.mark.parametrize("text", ["", "abc", "一二三四五", "?!", "蝶1", None, "😀"])
def test_bad_characters(text):
    with pytest.raises(ValueError):
        clean_characters(text)


@pytest.mark.parametrize(
    ("ch", "name"), [(ch, "kai") for ch in "蝶鬱龜"] + [(ch, "sans") for ch in "嘅咗囍"]
)
def test_fonts(ch, name):
    assert font_name_for(ch) == name


@pytest.mark.parametrize("chars", ["蝶", "嘅", "蝴蝶", "蜻蜓", "蝴蝶蜻", "蝴蝶蜻蜓"])
def test_render(chars):
    png = render_glyph_png(chars)
    image = Image.open(BytesIO(png))
    assert image.size == (240, 240)
    assert image.mode == "P"
    assert png[28] == 0  # IHDR interlace byte
    assert len(png) <= 24 * 1024
    grayscale = image.convert("L")
    ink = [
        (x, y) for y in range(240) for x in range(240) if grayscale.getpixel((x, y)) > 0
    ]
    assert ink
    assert all((x - 120) ** 2 + (y - 120) ** 2 <= 100**2 for x, y in ink)
    message = glyph_message(png)
    assert len(message["png"]) <= 32768
    assert message["hold_ms"] == 10000
    assert base64.b64decode(message["png"]) == png


@pytest.mark.parametrize("hold", [999, 60001, True, 1000.0])
def test_bad_hold(hold):
    with pytest.raises(ValueError):
        glyph_message(b"", hold)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["copy", "dictation", "bad", "offline", "render_failure"]
)
async def test_tool_device(monkeypatch, tmp_path, case):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.delenv("STACKCHAN_TOKEN", raising=False)
    forms = xiaoya_forms.XiaoyaForms(tmp_path / "forms.json")
    forms.add_items(
        ["中文默書" if case == "dictation" else "中文抄寫"], source="parent"
    )
    monkeypatch.setattr(xiaoya_forms, "_forms", forms)
    if case in {"render_failure", "dictation", "bad"}:
        from stackchan_mcp import xiaoya_glyph

        def fail(_chars):
            raise ValueError("unsupported glyph")

        monkeypatch.setattr(xiaoya_glyph, "render_glyph_png", fail)
    executor = VoiceToolExecutor()
    proxy = SimpleNamespace(
        start=AsyncMock(return_value=True),
        stop=AsyncMock(),
        send_device_json=AsyncMock(),
        connected=True,
    )
    manager = ESP32Manager(cloud_proxy_factory=lambda: proxy)
    await manager.start("127.0.0.1", 0)
    port = manager._server.sockets[0].getsockname()[1]
    try:
        if case == "offline":
            result = await executor.execute("show_character", {"character": "蝶"})
            assert result["ok"] is False
            return
        async with websockets.connect(f"ws://127.0.0.1:{port}") as device:
            await device.send(json.dumps({"type": "hello", "features": {"mcp": False}}))
            hello = json.loads(await asyncio.wait_for(device.recv(), 1))
            assert hello["type"] == "hello"
            await device.send(
                json.dumps({"type": "xiaoya", "event": "form_state", "form": 1})
            )
            for _ in range(100):
                if forms.session_ready:
                    break
                await asyncio.sleep(0.01)
            assert forms.session_ready
            result = await executor.execute(
                "show_character", {"character": "abc" if case == "bad" else "蝶"}
            )
            frames = []
            while True:
                try:
                    frames.append(
                        json.loads(await asyncio.wait_for(device.recv(), 0.1))
                    )
                except asyncio.TimeoutError:
                    break
            glyphs = [frame for frame in frames if frame.get("cmd") == "glyph"]
            if case == "copy":
                assert result["shown"] is True and result["seconds"] == 10
                assert len(glyphs) == 1
                assert glyphs[0]["session_id"] == hello["session_id"]
                assert Image.open(BytesIO(base64.b64decode(glyphs[0]["png"]))).size == (
                    240,
                    240,
                )
            else:
                assert glyphs == []
                assert result["ok"] is (case == "dictation")
                if case == "dictation":
                    assert result["shown"] is False and result["mode"] == "dictation"
    finally:
        await executor.close()
        await manager.stop()
        forms.unregister_sender()


@pytest.mark.asyncio
async def test_send_device_readiness_and_failure(tmp_path):
    forms = xiaoya_forms.XiaoyaForms(tmp_path / "forms.json")
    sender = AsyncMock(side_effect=ConnectionError("gone"))
    assert await forms.send_device({}) is False
    forms.register_sender(sender, push=False)
    try:
        assert await forms.send_device({}) is False
        sender.assert_not_awaited()
        forms._session_ready = True
        assert await forms.send_device({}) is False
    finally:
        forms.unregister_sender()


def test_non_child_catalog(monkeypatch):
    from stackchan_mcp.voice_tools import build_function_declarations
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "0")
    assert "show_character" not in {tool["name"] for tool in build_function_declarations()}


def test_font_directory_override(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_GLYPH_FONT_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        render_glyph_png("蝶")


def test_unsupported_character():
    with pytest.raises(ValueError):
        font_name_for("\U00031350")
