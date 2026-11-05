import asyncio
from datetime import datetime
import json
import os
import time
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp.usage_stats import UsageStats, HK, COUNTERS, format_usage
from stackchan_mcp.parent_api import create_parent_app
from stackchan_mcp.parent_cli import main, run_mcp
from stackchan_mcp.parent_settings import ParentSettings
from stackchan_mcp.debug_status import DebugStatus
from stackchan_mcp.gemini_voice_proxy import GeminiVoiceProxy


@pytest.fixture
def clock():
    now = [datetime(2026, 10, 22, 23, 59, 50, tzinfo=HK).timestamp()]
    return now


def test_schema_midnight_reopen_permissions_retention(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        store = UsageStats(tmp_path / "usage", lambda: clock[0])
        store.device(True)
        store.voice_session(True)
        store.wake()
        store.audio()
        store.audio()
        clock[0] += 20
        today = store.snapshot()
        yesterday = store.snapshot("2026-10-22")
        assert today["connected_seconds"] == today["voice_session_seconds"] == 10
        assert (
            yesterday["connected_seconds"] == yesterday["voice_session_seconds"] == 10
        )
        assert today["complete"] is True
        assert today["counting_since"] == "2026-10-23T00:00:00+08:00"
        assert yesterday["conversation_count"] == 1
        assert yesterday["wake_count"] == yesterday["wake_word_count"] == 1
        assert set(today) == {
            "date",
            "timezone",
            "day_start",
            "generated_at",
            "counting_since",
            "complete",
            "connected_now",
            "last_activity_at",
            *COUNTERS,
        }
        assert today["last_activity_at"] is None
        store.device(False)
        path = tmp_path / "usage" / "2026-10-23.json"
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert not json.loads(path.read_text())["connected_now"]
        clock[0] += 10
        reopened = UsageStats(path.parent, lambda: clock[0])
        assert reopened.snapshot()["connected_seconds"] == 10
        assert reopened.snapshot()["complete"] is False
        reopened.device(True)
        clock[0] += 5
        assert reopened.snapshot()["connected_seconds"] == 15
        (path.parent / "2026-10-09.json").write_text("{}")
        (path.parent / "2026-10-10.json").write_text("{}")
        reopened.flush()
        assert not (path.parent / "2026-10-09.json").exists()
        assert (path.parent / "2026-10-10.json").exists()
        assert reopened.snapshot("2026-10-11") is None
        for date in ["2026-10-09", "2026-10-24", "../secret", "2026-1-2"]:
            with pytest.raises(ValueError):
                reopened.snapshot(date)
    finally:
        os.environ.pop("TZ", None)
        if hasattr(time, "tzset"):
            time.tzset()


def test_text(tmp_path, clock):
    store = UsageStats(tmp_path, lambda: clock[0])
    r = store.snapshot()
    r.update(
        date="2026-10-22",
        connected_seconds=5420,
        connect_count=3,
        disconnect_count=2,
        connected_now=True,
        wake_count=14,
        wake_word_count=9,
        button_listen_count=5,
        conversation_count=11,
        voice_session_seconds=1830,
        local_handled_count=4,
        homework_photos_sent=2,
        homework_photos_failed=0,
        last_activity_at="2026-10-22T20:12:40+08:00",
        counting_since="2026-10-22T15:02:11+08:00",
    )
    assert (
        format_usage(r, r["date"])
        == """2026-10-22（香港時間）小芽用量：
連線時間：1 小時 30 分（連線 3 次、斷線 2 次，而家已連線）
叫醒聆聽：14 次（叫「小白」9 次、撳掣 5 次）
對話：11 次；語音時間：30 分
本地處理：4 次
功課相：送咗 2 張，送唔到 0 張
最後活動：20:12
（15:02 先開始計，之前 gateway 冇行）"""
    )
    assert format_usage(None, "2026-10-21") == "2026-10-21（香港時間）未有用量紀錄"
    r["complete"] = True
    assert "先開始計" not in format_usage(r, r["date"])


@pytest.mark.asyncio
async def test_usage_api_cli(tmp_path, clock, monkeypatch, capsys):
    from aiohttp.test_utils import make_mocked_request
    from stackchan_mcp.parent_cli import ParentClient

    store = UsageStats(tmp_path / "usage", lambda: clock[0])
    app = create_parent_app(
        "test-parent", ParentSettings(tmp_path / "settings.json"), usage=store
    )
    auth = {"Host": "localhost", "Authorization": "Bearer test-parent"}

    async def request(path, headers):
        req = make_mocked_request("GET", path, headers=headers)
        req._cache["remote"] = "127.0.0.1"
        match = await app.router.resolve(req)
        return await app.middlewares[0](req, match.handler)

    assert (await request("/parent/v1/usage", {"Host": "localhost"})).status == 401
    assert (await request("/parent/v1/usage?date=bad", auth)).status == 400
    assert (await request("/parent/v1/usage", {**auth, "Origin": "null"})).status == 403
    response = await request("/parent/v1/usage", auth)
    payload = json.loads(response.text)
    assert payload["usage"]["date"] == "2026-10-22"
    calls = []

    def call(self, endpoint, values=None):
        calls.append(endpoint)
        return payload

    monkeypatch.setattr(ParentClient, "call", call)
    assert main(["usage", "--text"]) == 0
    assert "小芽用量" in capsys.readouterr().out
    payload["usage"] = None
    assert main(["usage", "--date", "2026-10-21", "--text"]) == 0
    assert "未有用量紀錄" in capsys.readouterr().out
    assert calls == ["usage", "usage?date=2026-10-21"]


@pytest.mark.asyncio
async def test_periodic_and_shutdown(tmp_path, clock, monkeypatch):
    store = UsageStats(tmp_path, lambda: clock[0])
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            raise asyncio.CancelledError
        clock[0] += seconds

    monkeypatch.setattr("stackchan_mcp.usage_stats.asyncio.sleep", sleep)
    store.device(True)
    with pytest.raises(asyncio.CancelledError):
        await store.run()
    assert slept == [60, 60]
    assert (
        json.loads((tmp_path / "2026-10-23.json").read_text())["connected_seconds"]
        == 50
    )


@pytest.mark.asyncio
async def test_mcp_usage(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    async def inline(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline)
    calls = []

    class Client:
        def call(self, endpoint):
            calls.append(endpoint)
            return {"ok": True}

    async def run(server):
        assert "xiaoya_usage" in {t.name for t in await server.list_tools()}
        await server._tool_manager.get_tool("xiaoya_usage").fn()
        await server._tool_manager.get_tool("xiaoya_usage").fn(date="2026-10-22")

    monkeypatch.setattr(FastMCP, "run_stdio_async", run)
    await run_mcp(Client())
    assert calls == ["usage", "usage?date=2026-10-22"]


@pytest.mark.asyncio
async def test_auto_listen_and_event_hooks(tmp_path, clock, monkeypatch):
    from types import SimpleNamespace

    store = UsageStats(tmp_path, lambda: clock[0])
    debug = DebugStatus(usage=store)
    proxy = GeminiVoiceProxy(esp32_ref=None, debug_status=debug)
    proxy._bridge = SimpleNamespace(running=True, send_audio=AsyncMock())
    proxy._codec = object()
    proxy._send_to_device = AsyncMock()
    proxy._ready_event.set()
    gate = SimpleNamespace(open=lambda: None)
    monkeypatch.setattr(proxy, "_maybe_rebuild_wake_gate", lambda: gate)
    monkeypatch.setattr(proxy, "_signal_manual_vad_start", AsyncMock())
    monkeypatch.setattr(proxy, "_set_leds_safe", AsyncMock())
    monkeypatch.setattr(proxy, "_send_device_emotion_safe", AsyncMock())
    await proxy.send_device_json({"type": "listen", "state": "start", "mode": "auto"})
    assert store.snapshot()["wake_count"] == 0
    await proxy.send_device_json(
        {"type": "listen", "state": "detect", "source": "button"}
    )
    await proxy._send_upstream_pcm(b"audio")
    await proxy._send_upstream_pcm(b"audio")
    assert store.snapshot()["button_listen_count"] == 1
    assert store.snapshot()["conversation_count"] == 1
    debug.on_wake_closed()
    debug.on_wake_word_hit()
    assert store.snapshot()["wake_count"] == 2
    debug.on_device_connected()
    debug.on_gemini_connected(1)
    clock[0] += 2
    debug.on_gemini_stopped()
    debug.on_tts_state(True)
    debug.on_device_disconnected()
    assert store.snapshot()["voice_session_seconds"] == 2
    assert store.snapshot()["last_activity_at"] is not None


@pytest.mark.parametrize("sent", [True, False])
def test_photo_counters(tmp_path, clock, monkeypatch, sent):
    from types import SimpleNamespace
    from stackchan_mcp.homework_photo import HomeworkPhoto
    from stackchan_mcp import debug_status

    store = UsageStats(tmp_path / "usage", lambda: clock[0])
    monkeypatch.setattr(debug_status, "_status", DebugStatus(usage=store))
    forms = SimpleNamespace(
        mark_done=lambda *a, **k: dict(
            already_done=False, done=1, total=1, level_up=False
        )
    )

    def send(*args):
        if not sent:
            raise OSError("offline")

    photo = HomeworkPhoto(
        forms=forms,
        presence=SimpleNamespace(set_suppress=lambda *a: None),
        send=send,
        captures_dir=tmp_path / "captures",
    )
    photo.item = {"id": 1, "title": "private title"}
    photo.complete(True, "private-path.jpg")
    record = store.snapshot()
    assert record["homework_photos_sent"] == int(sent)
    assert record["homework_photos_failed"] == int(not sent)
    assert "private" not in json.dumps(record)
