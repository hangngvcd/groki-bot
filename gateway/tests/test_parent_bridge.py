# SPDX-License-Identifier: MIT
import asyncio
import json
import logging
from unittest.mock import AsyncMock
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
import pytest

from stackchan_mcp.debug_status import DebugStatus
from stackchan_mcp.parent_settings import ParentSettings, device_support
from stackchan_mcp.parent_api import (
    build_parent_status,
    create_parent_app,
    parent_api_enabled,
    resolve_parent_token,
    start_parent_api,
)
from stackchan_mcp.parent_cli import main, cli_value


@pytest.fixture
def store(tmp_path):
    return ParentSettings(tmp_path / "private" / "settings.json")


def test_settings(store, caplog, monkeypatch):
    assert store.get() == dict(
        volume=60, rest_after_min=5, camera_presence=False, notifications=False
    )
    events = []
    store.add_listener(lambda settings, keys: events.append((settings, keys)))
    with caplog.at_level(logging.INFO):
        result = store.set("volume", 40, source="parent-api")
    assert result["changed"] == {"volume": {"old": 60, "new": 40}}
    assert "Parent setting changed: volume 60 -> 40 (source=parent-api)" in caplog.text
    assert events[0][1] == {"volume"}
    assert ParentSettings(store.path).get()["volume"] == 40
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    original = store.path.read_text()

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr("os.replace", fail)
    with pytest.raises(OSError):
        store.set("volume", 30, source="test")
    assert store.path.read_text() == original
    assert store.get()["volume"] == 40
    assert not list(store.path.parent.glob(".parent-settings-*"))


@pytest.mark.parametrize(
    "values",
    [
        {"volume": True},
        {"volume": -1},
        {"volume": 101},
        {"volume": "40"},
        {"rest_after_min": 0},
        {"rest_after_min": 61},
        {"camera_presence": "off"},
        {"notifications": 0},
        {"unknown": 1},
        [],
        {},
    ],
)
def test_invalid_atomic(store, values):
    with pytest.raises(ValueError):
        store.update(values, source="test")
    assert store.get()["volume"] == 60
    assert not store.path.exists()


def test_corrupt(store, caplog):
    store.path.parent.mkdir()
    store.path.write_text("{broken")
    with caplog.at_level(logging.WARNING):
        assert ParentSettings(store.path).get()["volume"] == 60
    assert "corrupt" in caplog.text
    store.path.write_text('{"volume": 30, "notifications": "false"}')
    assert ParentSettings(store.path).get()["volume"] == 60


@pytest.mark.asyncio
async def test_pushers(store):
    pusher = AsyncMock()
    store.register_pusher("volume", pusher)
    store.set("volume", 40, source="test")
    await asyncio.gather(*store._tasks)
    pusher.assert_awaited_once_with(40)
    await store.push_to_device()
    assert pusher.await_count == 2
    assert all(not item["applied_to_device"] for item in device_support().values())


def test_pure_status(store):
    status = DebugStatus(clock=lambda: 123)
    status.on_device_connected("secret-mac")
    status.record_transcript("private transcript")
    status.on_device_battery(25)
    status.listening_mode = "wake_word"
    assert status.snapshot()["device"]["battery_at"] == 123
    payload = build_parent_status(
        status.snapshot(), store.get(), {"STACKCHAN_WAKE_PHRASE": "小白"}, {}
    )
    encoded = json.dumps(payload)
    assert (
        "device_id" not in encoded
        and "transcript" not in encoded
        and "secret-mac" not in encoded
    )
    assert payload["voice"]["backend"] == "gemini"
    assert payload["device"]["battery_percent"] == 25
    assert "小白" in payload["summary_zh"] and "25%" in payload["summary_zh"]
    status.listening_mode = "wake_word_or_button"
    payload = build_parent_status(
        status.snapshot(), store.get(), {"STACKCHAN_WAKE_PHRASE": "小白"}, {}
    )
    assert "叫「小白」或者撳掣" in payload["summary_zh"]
    status.listening_mode = "button_only"
    payload = build_parent_status(status.snapshot(), store.get(), {}, {})
    assert "只准撳掣" in payload["summary_zh"]
    payload = build_parent_status(
        DebugStatus().snapshot(),
        store.get(),
        {},
        {"reachable": True, "agent_configured": True},
    )
    assert "未有資料" in payload["summary_zh"]
    assert payload["voice"]["login_helper"]["reachable"]
    assert payload["voice"]["login_helper"]["agent_configured"]
    assert not payload["voice"]["login_helper"]["desktop_enabled"]


@pytest.mark.asyncio
async def test_api_and_cli(store, monkeypatch, caplog, capsys):
    monkeypatch.setenv("STACKCHAN_TOKEN", "child-secret")
    monkeypatch.setenv("STACKCHAN_PARENT_TOKEN", "parent-secret")
    async with TestClient(
        TestServer(create_parent_app("parent-secret", store))
    ) as client:
        url = "/parent/v1/settings"
        auth = {"Authorization": "Bearer parent-secret"}
        for headers, expected in [
            ({}, 401),
            ({"Authorization": "Bearer child-secret"}, 401),
            ({**auth, "Origin": "http://localhost"}, 403),
            ({**auth, "Host": "evil.example"}, 403),
        ]:
            assert (await client.get(url, headers=headers)).status == expected
        with caplog.at_level(logging.INFO):
            response = await client.post(url, headers=auth, json={"volume": 40})
        assert response.status == 200
        assert "source=parent-api" in caplog.text
        assert (
            await client.post(
                url, headers=auth, json={"volume": 30, "notifications": "on"}
            )
        ).status == 400
        assert store.get()["volume"] == 40
        response = await client.get("/parent/v1/status", headers=auth)
        assert "device_id" not in await response.text()
        base = str(client.make_url("")).rstrip("/")
        assert await asyncio.to_thread(main, ["--url", base, "status", "--text"]) == 0
        assert "小芽" in capsys.readouterr().out
        assert (
            await asyncio.to_thread(
                main, ["--url", base, "set", "camera_presence", "off"]
            )
            == 0
        )
        monkeypatch.setenv("STACKCHAN_PARENT_TOKEN", "wrong")
        assert await asyncio.to_thread(main, ["--url", base, "status"]) == 1
        assert "401" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_gating_and_token(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKCHAN_PARENT_API", raising=False)
    assert not parent_api_enabled()
    assert await start_parent_api() is None
    monkeypatch.delenv("STACKCHAN_PARENT_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STACKCHAN_PARENT_TOKEN_FILE", raising=False)
    token = resolve_parent_token()
    path = tmp_path / ".stackchan/parent_token"
    assert path.stat().st_mode & 0o777 == 0o600
    assert resolve_parent_token() == token
    monkeypatch.setenv("STACKCHAN_TOKEN", token)
    monkeypatch.setenv("STACKCHAN_PARENT_API", "1")
    # The gateway keeps running; only the parent API is refused.
    assert await start_parent_api() is None


@pytest.mark.parametrize("child", ["0", "1"])
@pytest.mark.asyncio
async def test_voice_catalogs(monkeypatch, child):
    from stackchan_mcp.voice_tools import VoiceToolExecutor
    from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge
    from stackchan_mcp.gemini_live_bridge import GeminiLiveBridge

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", child)
    monkeypatch.setenv("STACKCHAN_MAC_CONTROL", "0")
    monkeypatch.setenv("STACKCHAN_ASK_CLAUDE", "0")
    device = SimpleNamespace(device_connected=False)
    gemini = GeminiLiveBridge(device, api_key="test")
    shared = VoiceToolExecutor(device)
    gbot = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
        tool_executor=shared,
    )
    gbot_tools = gbot._configure_tools({"tools": []})
    assert not any(
        "setting" in tool["name"] or "volume" in tool["name"] for tool in gbot_tools
    )
    try:
        for executor in (gemini, shared):
            names = {tool["name"] for tool in executor.declarations()}
            assert not names & {
                "xiaoya_set_setting",
                "set_setting",
                "set_settings",
                "set_volume",
                "set_camera_presence",
                "set_rest_after_min",
                "set_notifications",
            }
        if child == "1":
            assert "呢樣要問媽媽或者爸爸幫你改㗎。" in shared.instructions()
            assert (
                "呢樣要問媽媽或者爸爸幫你改㗎。" in gbot._provider_tool_instructions()
            )
    finally:
        await gemini.stop()
        await gbot.stop()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("off", False),
        ("ON", True),
        ("false", False),
        ("true", True),
        ("1", True),
        ("0", False),
    ],
)
def test_cli_boolean(value, expected):
    assert cli_value("camera_presence", value) is expected


@pytest.mark.asyncio
async def test_handlers_without_sockets(store, monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setenv("STACKCHAN_TOKEN", "device")
    app = create_parent_app("parent", store)
    guard = app.middlewares[0]

    async def request(method, path, headers, body=None, remote="127.0.0.1"):
        req = make_mocked_request(method, path, headers=headers)
        req._cache["remote"] = remote
        if body is not None:
            req.json = AsyncMock(return_value=body)
        match = await app.router.resolve(req)
        return await guard(req, match.handler)

    path = "/parent/v1/settings"
    auth = {"Host": "127.0.0.1:18772", "Authorization": "Bearer parent"}
    assert (await request("GET", path, {"Host": "localhost"})).status == 401
    assert (
        await request("GET", path, {**auth, "Authorization": "Bearer device"})
    ).status == 401
    assert (await request("GET", path, {**auth, "Origin": "null"})).status == 403
    assert (await request("GET", path, {**auth, "Host": "evil"})).status == 403
    assert (await request("GET", path, auth, remote="192.168.1.2")).status == 403
    response = await request("POST", path, auth, {"volume": 40})
    assert response.status == 200
    assert json.loads(response.text)["changed"]["volume"]["new"] == 40
    assert (await request("POST", path, auth, {"volume": 10, "bad": 1})).status == 400
    assert store.get()["volume"] == 40
    response = await request("GET", "/parent/v1/status", auth)
    assert response.status == 200
    assert "transcripts" not in response.text and "device_id" not in response.text


@pytest.mark.asyncio
async def test_gbot_status_uses_snapshot(store):
    from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge

    debug = DebugStatus()
    bridge = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
        debug_status=debug,
    )
    await bridge._handle_event({"type": "session.updated"})
    result = build_parent_status(
        debug.snapshot(), store.get(), {"STACKCHAN_VOICE_BACKEND": "gbot"}, {}
    )
    assert result["voice"]["session_connected"]
    await bridge.stop()
    assert not debug.snapshot()["gemini"]["connected"]


def test_cli_errors_and_token_file(tmp_path, monkeypatch, capsys):
    from io import BytesIO
    from unittest.mock import Mock
    from urllib.error import HTTPError, URLError
    from stackchan_mcp import parent_cli

    monkeypatch.delenv("STACKCHAN_PARENT_TOKEN", raising=False)
    monkeypatch.setenv("STACKCHAN_PARENT_TOKEN_FILE", str(tmp_path / "token"))
    assert main(["status"]) == 1
    assert "gateway 未行 / parent API 未開" in capsys.readouterr().err
    (tmp_path / "token").write_text("secret")
    opener = Mock()
    monkeypatch.setattr(parent_cli, "build_opener", Mock(return_value=opener))
    opener.open.side_effect = URLError("unreachable")
    assert main(["status"]) == 1
    opener.open.side_effect = HTTPError(
        "http://localhost", 401, "Unauthorized", {}, None
    )
    assert main(["status"]) == 1
    assert "401" in capsys.readouterr().err
    opener.open.side_effect = None
    opener.open.return_value = BytesIO(b'{"ok":true,"changed":{},"settings":{}}')
    assert main(["set", "volume", "40"]) == 0
    request = opener.open.call_args.args[0]
    assert json.loads(request.data) == {"volume": 40}
    assert request.get_header("Authorization") == "Bearer secret"
    assert "secret" not in capsys.readouterr().out
    assert main(["--url", "http://example.com", "status"]) == 1


def test_singleton(tmp_path, monkeypatch):
    from stackchan_mcp.parent_settings import get_parent_settings, reset_parent_settings

    monkeypatch.setenv(
        "STACKCHAN_PARENT_SETTINGS_PATH", str(tmp_path / "settings.json")
    )
    reset_parent_settings()
    first = get_parent_settings()
    assert get_parent_settings() is first
    reset_parent_settings()
    assert get_parent_settings() is not first
    reset_parent_settings()
