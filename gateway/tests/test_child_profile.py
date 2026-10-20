# SPDX-License-Identifier: MIT
"""Child policy contracts at the voice/device and HTTP boundaries (Seam G)."""

import asyncio
import json
import logging
import socket
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
import websockets
from aiohttp.test_utils import TestClient, TestServer
from mcp.types import CallToolRequest, ListToolsRequest

from stackchan_mcp import cli, gateway as gateway_module, voice_tools
from stackchan_mcp.capture_server import create_capture_app
from stackchan_mcp.cloud_proxy import CloudConnectionInfo
from stackchan_mcp.demo_reactions import DemoReactions
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge
from stackchan_mcp.gbot_voice_proxy import GbotVoiceProxy
from stackchan_mcp.gemini_live_bridge import build_live_config
from stackchan_mcp.idle_behavior import IdleBehavior
from stackchan_mcp.stdio_server import create_server
from stackchan_mcp.touch_bridge import TouchEvent
from test_gemini_voice_proxy import (
    FakeBridge,
    FakeCodec,
    FakeEdgeTTSProvider,
    FakeVoiceOnlyESP32,
)


RESTRICTED_FLAGS = (
    "STACKCHAN_MAC_CONTROL",
    "STACKCHAN_ASK_CLAUDE",
    "STACKCHAN_GEMINI_DEVICE_TOOLS",
    "STACKCHAN_FACE_TRACKER_AUTOSTART",
    "STACKCHAN_HEAD_FOLLOW_DEFAULT",
    "STACKCHAN_USB_TRANSPORT",
    "STACKCHAN_GATEWAY_IDLE_ENABLED",
    "STACKCHAN_DEMO_REACTIONS",
)


@pytest.fixture
def child(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    for name in RESTRICTED_FLAGS:
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("STACKCHAN_WAKE_WORD", "0")
    monkeypatch.setenv("STACKCHAN_TOKEN", "child-secret")
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", "gbot")
    monkeypatch.setenv("STACKCHAN_TOOL_BOT", "xiaoya")
    monkeypatch.setenv("STACKCHAN_TOOL_BOT_ID", "agent")
    monkeypatch.setenv("STACKCHAN_GBOT_VOICE_BOT_ID", "agent")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class VoiceSocket:
    """Fake provider socket: records the actual session.update catalog."""

    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, event):
        self.sent.append(event)

    async def close(self):
        self.closed = True

    async def __aiter__(self):
        yield SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT, data='{"type":"session.updated"}'
        )
        await asyncio.Event().wait()


class LoginHTTP:
    def __init__(self, ws, native_tools):
        self.ws, self.native_tools = ws, native_tools

    @asynccontextmanager
    async def request(self, method, url, **kwargs):
        if url.endswith("/credential"):
            result = {
                "credential": {
                    "websocketUrl": "wss://api.x.ai/v1/realtime",
                    "model": "fixture",
                    "clientSecret": "fixture",
                    "expiresAtUnixSeconds": time.time() + 60,
                }
            }
        else:
            assert url.endswith("/session")
            result = {
                "instructions": "fixture",
                "tools": [
                    {"type": "function", "name": name, "parameters": {"type": "object"}}
                    for name in self.native_tools
                ],
            }
        yield SimpleNamespace(status=200, json=AsyncMock(return_value=result))

    @asynccontextmanager
    async def post(self, url, **kwargs):
        # Native channel availability does not affect the advertised catalog.
        yield SimpleNamespace(status=503)

    async def ws_connect(self, *args, **kwargs):
        return self.ws

    async def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("native_end", [True, False])
async def test_gbot_receives_only_child_catalog(child, monkeypatch, native_end):
    from stackchan_mcp import gbot_voice_bridge

    ws = VoiceSocket()
    names = ["send_task", "search_conversations", "camera_capture", "ask_claude"]
    if native_end:
        names.append("end_the_call")
    http = LoginHTTP(ws, names)
    monkeypatch.setattr(
        gbot_voice_bridge.aiohttp, "ClientSession", lambda **kwargs: http
    )
    monkeypatch.setattr(voice_tools, "find_claude_bin", lambda: "claude")
    mac = Mock(side_effect=AssertionError("Mac control must not start"))
    monkeypatch.setattr(voice_tools, "MacController", mac)
    executor_factory = Mock(wraps=voice_tools.VoiceToolExecutor)
    proxy = GbotVoiceProxy(
        esp32_ref=lambda: FakeVoiceOnlyESP32(),
        bridge_factory=GbotVoiceBridge,
        wake_gate_factory=lambda: None,
        codec_factory=FakeCodec,
        edge_tts_provider_factory=FakeEdgeTTSProvider,
        voice_tool_executor_factory=executor_factory,
    )
    try:
        assert await proxy.start(
            CloudConnectionInfo("wss://unused.invalid", "", "1", "device", "client"),
            {},
            AsyncMock(),
        )
        config = next(
            event["session"] for event in ws.sent if event["type"] == "session.update"
        )
        assert {tool["name"] for tool in config["tools"]} == {
            "send_task",
            "search_conversations",
            "end_the_call" if native_end else "end_conversation",
        }
        executor_factory.assert_called_once()
        mac.assert_not_called()
    finally:
        await proxy.stop()


def test_gemini_child_catalog_excludes_search_and_other_tools(child):
    config = build_live_config()
    assert all(tool.google_search is None for tool in config.tools)
    assert {
        declaration.name
        for tool in config.tools
        for declaration in tool.function_declarations or []
    } == {
        "end_conversation",
    }


@pytest.mark.asyncio
async def test_extensions_cannot_broaden_child_catalog(child):
    dispatch = AsyncMock()
    extension = voice_tools.ToolExtension(
        declarations=({"name": "camera_capture", "parameters": {}},),
        dispatch=dispatch,
    )
    executor = voice_tools.VoiceToolExecutor(extensions=(extension,))
    try:
        assert {entry["name"] for entry in executor.declarations()} == {
            "end_conversation"
        }
        assert (await executor.execute("camera_capture", {}))["ok"] is False
        dispatch.assert_not_awaited()
    finally:
        await executor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raise", "not_connected"])
@pytest.mark.parametrize("profile", ["0", "1"])
async def test_failed_gbot_has_no_backend_fallback(monkeypatch, profile, failure):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", profile)
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", "gbot")
    monkeypatch.setenv("STACKCHAN_TOKEN", "child-secret")
    bridge = FakeBridge()
    if failure == "raise":
        bridge.start = AsyncMock(side_effect=RuntimeError("login unavailable"))
    else:
        bridge.wait_connected = AsyncMock(return_value=False)
    factory = Mock(
        side_effect=lambda **kwargs: GbotVoiceProxy(
            **kwargs,
            bridge_factory=lambda **options: bridge,
            wake_gate_factory=lambda: None,
            codec_factory=FakeCodec,
            edge_tts_provider_factory=FakeEdgeTTSProvider,
        )
    )
    fallback = Mock(side_effect=AssertionError("Gemini fallback must not start"))
    monkeypatch.setattr(gateway_module, "GbotVoiceProxy", factory)
    monkeypatch.setattr(gateway_module, "GeminiVoiceProxy", fallback)
    gateway = gateway_module.Gateway()
    port = free_port()
    await gateway.esp32.start("127.0.0.1", port)
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}",
            additional_headers={"Authorization": "Bearer child-secret"},
        ) as device:
            await device.send(json.dumps({"type": "hello", "features": {"mcp": False}}))
            if profile == "1":
                with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
                    await asyncio.wait_for(device.recv(), 1)
                assert closed.value.rcvd.code == 1011
                assert "Voice backend unavailable" in closed.value.rcvd.reason
                assert not gateway.esp32.device_connected
            else:
                assert (
                    json.loads(await asyncio.wait_for(device.recv(), 1))["type"]
                    == "hello"
                )
            factory.assert_called_once()
            fallback.assert_not_called()
    finally:
        await gateway.esp32.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["unknown", "xiaozhi"])
async def test_unsupported_backend_closes_without_defaulting(
    child, monkeypatch, backend
):
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", backend)
    fallback = Mock(side_effect=AssertionError("fallback must not start"))
    monkeypatch.setattr(gateway_module, "GeminiVoiceProxy", fallback)
    gateway = gateway_module.Gateway()
    port = free_port()
    await gateway.esp32.start("127.0.0.1", port)
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}",
            additional_headers={"Authorization": "Bearer child-secret"},
        ) as device:
            await device.send('{"type":"hello","features":{"mcp":false}}')
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(device.recv(), 1)
        fallback.assert_not_called()
    finally:
        await gateway.esp32.stop()


@pytest.mark.asyncio
async def test_gateway_never_launches_camera_or_usb(
    child, monkeypatch, tmp_path, caplog
):
    from stackchan_mcp import face_tracker

    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "0")
    monkeypatch.setenv("WS_PORT", str(free_port()))
    http_port = free_port()
    monkeypatch.setenv("CAPTURE_PORT", str(http_port))
    # A usable binary must still be suppressed, including explicit tracking requests.
    binary = tmp_path / "tracker"
    binary.touch(mode=0o755)
    monkeypatch.setattr(face_tracker, "default_tracker_binary", lambda: binary)
    spawn = AsyncMock(side_effect=AssertionError("camera process must not spawn"))
    monkeypatch.setattr(face_tracker.asyncio, "create_subprocess_exec", spawn)
    usb = Mock(side_effect=AssertionError("USB must not open"))
    monkeypatch.setattr(gateway_module, "UsbTransport", usb)
    gateway = gateway_module.Gateway()
    try:
        await gateway.start()
        assert (await gateway.start_face_tracking())["ok"] is False
        async with aiohttp.ClientSession() as http:
            for path in ("/capture", "/track"):
                async with http.post(
                    f"http://127.0.0.1:{http_port}{path}", json={}
                ) as response:
                    assert response.status == 404
        assert not gateway.face_tracking_status()["tracker_running"]
        assert not gateway.face_tracking_status()["head_follow"]
        spawn.assert_not_awaited()
        usb.assert_not_called()
        for name in RESTRICTED_FLAGS:
            assert f"{name}: conflicts with child profile" in caplog.text
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_proactive_features_never_reach_device(child):
    device = SimpleNamespace(
        device_connected=True, call_tool=AsyncMock(), speak=AsyncMock()
    )
    idle = IdleBehavior(device)
    demo = DemoReactions(device, enabled=True)
    try:
        await idle.start()
        assert not await idle.step_once()
        assert not demo.maybe_face_entered({"confidence": 1})
        assert not demo.maybe_head_tap(TouchEvent("tap", 0, 1, 1))
        demo.spawn_intro()
        demo.spawn_face_reaction()
        demo.spawn_touch_reaction()
        await asyncio.sleep(0)
        device.call_tool.assert_not_awaited()
        device.speak.assert_not_awaited()
    finally:
        await idle.stop()
        await demo.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "child-secret"])
async def test_inject_text_requires_stackchan_token(child, monkeypatch, token):
    monkeypatch.setenv("STACKCHAN_TOKEN", token)
    monkeypatch.setenv("VISION_TOKEN", "vision-only")
    monkeypatch.setenv("BEARER_TOKEN", "legacy-only")
    handler = AsyncMock(return_value=True)
    app = create_capture_app(capture_token="vision-only", inject_text_handler=handler)
    async with TestClient(TestServer(app)) as http:
        for auth in (None, "vision-only", "legacy-only", "wrong"):
            response = await http.post(
                "/debug/inject-text",
                json={"text": "hello"},
                headers={"Authorization": f"Bearer {auth}"} if auth else {},
            )
            assert response.status == (401 if token else 403)
        handler.assert_not_awaited()
        response = await http.post(
            "/debug/inject-text",
            json={"text": "hello"},
            headers={"Authorization": "Bearer child-secret"},
        )
        assert response.status == (200 if token else 403)
        if token:
            handler.assert_awaited_once_with("hello")


@pytest.mark.asyncio
async def test_camera_tools_are_hidden_and_refused(child):
    server = create_server()
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list")
    )
    assert "take_photo" not in {tool.name for tool in result.root.tools}
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params={"name": "take_photo", "arguments": {}},
        )
    )
    assert "child profile" in result.root.content[0].text
    gateway = gateway_module.Gateway()
    result, error = await gateway.esp32.call_tool("self.camera.take_photo", {})
    assert result is None and "child profile" in error["message"]


def test_check_reports_overridden_settings_and_missing_token(
    child, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_check_port", lambda *args: (True, None))
    monkeypatch.delenv("STACKCHAN_TOKEN")
    monkeypatch.setenv("BEARER_TOKEN", "legacy-secret")
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", "unknown")
    monkeypatch.setenv("VISION_HOST", "192.0.2.1")
    monkeypatch.setenv(
        "VISION_URL", "https://example.invalid/capture?token=vision-secret"
    )
    assert cli._run_preflight() == 1
    report = capsys.readouterr().out
    for name in (
        *RESTRICTED_FLAGS,
        "STACKCHAN_TOKEN",
        "STACKCHAN_VOICE_BACKEND",
        "VISION_HOST",
        "VISION_URL",
    ):
        assert f"{name}:" in report
    assert "legacy-secret" not in report
    assert "vision-secret" not in report
    assert "no fallback" in report


def test_check_warns_without_failing_for_enforced_overrides(child, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_check_port", lambda *args: (True, None))
    assert cli._run_preflight() == 0
    report = capsys.readouterr().out
    assert "restrictions enforced" in report
    assert "forced off" in report
    assert "child-secret" not in report


def test_profile_defaults_off(monkeypatch):
    monkeypatch.delenv("STACKCHAN_CHILD_PROFILE", raising=False)
    monkeypatch.setenv("STACKCHAN_MAC_CONTROL", "1")
    monkeypatch.setenv("STACKCHAN_GEMINI_DEVICE_TOOLS", "1")
    names = {entry["name"] for entry in voice_tools.VoiceToolExecutor().declarations()}
    assert {"get_current_datetime", "move_head", "open_app"} <= names
