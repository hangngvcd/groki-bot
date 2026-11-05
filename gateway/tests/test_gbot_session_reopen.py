# SPDX-License-Identifier: MIT
"""Grok voice ends idle sessions (900 s timeout); the next wake must reopen one."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp import gbot_voice_bridge as bridge_module
from stackchan_mcp.device_emotion import DEAD_LED_RGB
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge
from stackchan_mcp.gbot_voice_proxy import GbotVoiceProxy
from stackchan_mcp.wake_gate import WakeGate

FRAME = b"\x10\x00" * 320  # 20 ms of 16 kHz s16


class Codec:
    def reset_encode_buffer(self):
        pass


class FakeKws:
    available = True

    def __init__(self):
        self.fire = False

    def detect(self, pcm):
        fired, self.fire = self.fire, False
        return fired

    def reset(self):
        pass


class FakeBridge:
    instances = []

    def __init__(self, esp32=None, **kwargs):
        self.kwargs = kwargs
        self.running = False
        self._connected_event = asyncio.Event()
        self.audio = []
        self.stopped = False
        self.connect_delay = 0.0
        self.fail = False
        FakeBridge.instances.append(self)

    async def start(self):
        if self.fail:
            raise RuntimeError("fixture start failure")
        await asyncio.sleep(self.connect_delay)
        self.running = True
        self._connected_event.set()

    async def wait_connected(self, timeout=10.0):
        return self._connected_event.is_set()

    async def send_audio(self, pcm):
        if not self.running:
            raise RuntimeError("Grok Bot voice is disconnected")
        self.audio.append(pcm)

    async def stop(self):
        self.stopped = True
        self.running = False

    async def server_timeout(self):
        """What GbotVoiceBridge._receive does when the server ends the session."""
        self.running = False
        self._connected_event.clear()
        await self.kwargs["on_session_dead"]()


async def started_proxy(monkeypatch, *, button_only=False, delay=0.0):
    if button_only:
        monkeypatch.setenv("STACKCHAN_BUTTON_ONLY_LISTEN", "1")
    else:
        monkeypatch.delenv("STACKCHAN_BUTTON_ONLY_LISTEN", raising=False)
    monkeypatch.delenv("STACKCHAN_GBOT_SESSION_REOPEN", raising=False)
    monkeypatch.setenv("STACKCHAN_MANUAL_VAD", "0")
    FakeBridge.instances = []
    kws = FakeKws()
    gate = WakeGate(kws=kws)

    def factory(**kwargs):
        b = FakeBridge(**kwargs)
        b.connect_delay = delay
        return b

    proxy = GbotVoiceProxy(
        bridge_factory=factory,
        codec_factory=Codec,
        edge_tts_provider_factory=lambda **kwargs: SimpleNamespace(abort=AsyncMock()),
        wake_gate_factory=lambda: gate,
    )
    proxy.wake_confirm_hold_s = 0
    leds = []

    async def set_leds(*rgb):
        leds.append(tuple(rgb))

    proxy._set_leds_safe = set_leds
    proxy._send_device_emotion_safe = AsyncMock()
    proxy._signal_wake_chime = lambda: None
    assert await proxy.start(SimpleNamespace(device_id="fixture"), {}, AsyncMock())
    return proxy, kws, leds


async def wait_reopened(proxy):
    task = proxy._reopen_task
    assert task is not None
    assert await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_wake_word_after_server_timeout_reopens_and_delivers_waking_turn(monkeypatch):
    proxy, kws, leds = await started_proxy(monkeypatch, delay=0.05)
    try:
        first = FakeBridge.instances[0]
        await first.server_timeout()
        assert not proxy.connected
        assert DEAD_LED_RGB not in leds  # idle, not the red "dead" light
        assert leds[-1] == (0, 0, 0)

        # Wake word: the pre-roll plus the following speech must reach the new session.
        await proxy._forward_gated_device_pcm(b"\x01\x00" * 320)
        kws.fire = True
        await proxy._forward_gated_device_pcm(b"\x02\x00" * 320)
        await proxy._forward_gated_device_pcm(b"\x03\x00" * 320)
        assert proxy._reopen_in_progress()
        await proxy._forward_gated_device_pcm(b"\x04\x00" * 320)
        await wait_reopened(proxy)
        await proxy._forward_gated_device_pcm(b"\x05\x00" * 320)

        second = FakeBridge.instances[1]
        assert first.stopped and first.audio == []
        assert proxy.connected and proxy._bridge is second
        assert [c[:1] for c in second.audio] == [b"\x01", b"\x02", b"\x03", b"\x04", b"\x05"]
        assert DEAD_LED_RGB not in leds
        assert len(FakeBridge.instances) == 2  # one reopen, no keepalive churn
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_boot_button_after_server_timeout_reopens_session(monkeypatch):
    proxy, kws, leds = await started_proxy(monkeypatch, button_only=True, delay=0.02)
    try:
        await FakeBridge.instances[0].server_timeout()
        handled = await proxy.send_device_json(
            {"type": "listen", "state": "detect", "source": "button"}
        )
        assert handled
        assert proxy._wake_gate.is_listening
        await proxy._forward_gated_device_pcm(FRAME)
        await wait_reopened(proxy)
        await proxy._forward_gated_device_pcm(FRAME)
        second = FakeBridge.instances[1]
        assert second.audio == [FRAME, FRAME]
        assert proxy.connected
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_dead_session_without_wake_opens_nothing(monkeypatch):
    proxy, kws, leds = await started_proxy(monkeypatch)
    try:
        await FakeBridge.instances[0].server_timeout()
        for _ in range(5):
            await proxy._forward_gated_device_pcm(FRAME)  # dormant room noise
        assert len(FakeBridge.instances) == 1
        assert proxy._reopen_task is None
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_failed_reopen_shows_red_then_retries_on_later_wake(monkeypatch):
    proxy, kws, leds = await started_proxy(monkeypatch)
    proxy.session_reopen_retry_s = 0
    try:
        await FakeBridge.instances[0].server_timeout()
        real_new = proxy._new_bridge

        def failing():
            b = real_new()
            b.fail = True
            return b

        proxy._new_bridge = failing
        kws.fire = True
        await proxy._forward_gated_device_pcm(FRAME)
        assert not await asyncio.wait_for(proxy._reopen_task, 2)
        assert leds[-1] == DEAD_LED_RGB
        proxy._new_bridge = real_new
        assert await proxy.send_device_json({"type": "listen", "state": "start"})
        await wait_reopened(proxy)
        assert proxy.connected and len(FakeBridge.instances) == 3
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_deliberate_stop_never_reopens(monkeypatch):
    proxy, kws, leds = await started_proxy(monkeypatch)
    await proxy.stop()
    await proxy._handle_session_dead()
    assert not proxy._begin_session_reopen("fixture")
    assert len(FakeBridge.instances) == 1


@pytest.mark.asyncio
async def test_reopen_can_be_disabled(monkeypatch):
    proxy, kws, leds = await started_proxy(monkeypatch)
    monkeypatch.setenv("STACKCHAN_GBOT_SESSION_REOPEN", "0")
    try:
        await FakeBridge.instances[0].server_timeout()
        assert leds[-1] == DEAD_LED_RGB  # previous behaviour: red, stays dead
        assert not await proxy.send_device_json({"type": "listen", "state": "start"})
        await asyncio.sleep(0)
        assert len(FakeBridge.instances) == 1
    finally:
        await proxy.stop()


class FakeWs:
    def __init__(self, events, gate_at=None):
        self.closed = False
        self.gate_at = gate_at
        self.release = asyncio.Event()
        self.sent = []
        self._events = list(events)
        self._hang = asyncio.Event()

    async def send_json(self, event):
        self.sent.append(event)

    async def close(self):
        self.closed = True
        self._hang.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.gate_at is not None and len(self._events) == self.gate_at:
            await self.release.wait()
            self.gate_at = None
        if self._events:
            return SimpleNamespace(
                type=bridge_module.aiohttp.WSMsgType.TEXT,
                data=json.dumps(self._events.pop(0)),
            )
        await self._hang.wait()  # server keeps the socket open
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_real_bridge_timeout_error_then_wake_opens_new_session(monkeypatch):
    """Server says 'Conversation timed out' -> next wake posts a new /session."""
    monkeypatch.delenv("STACKCHAN_BUTTON_ONLY_LISTEN", raising=False)
    monkeypatch.delenv("STACKCHAN_GBOT_SESSION_REOPEN", raising=False)
    monkeypatch.delenv("STACKCHAN_TOOL_BOT", raising=False)
    monkeypatch.setenv("STACKCHAN_MANUAL_VAD", "0")
    timeout_error = {
        "type": "error",
        "error": {
            "type": "server_error",
            "code": "timeout",
            "message": "Conversation timed out after 900.0 seconds due to inactivity",
        },
    }
    sockets = [
        FakeWs([{"type": "session.updated"}, timeout_error], gate_at=1),
        FakeWs([{"type": "session.updated"}]),
    ]
    first_ws, second_ws = sockets
    sessions = []

    async def local(self, path, payload=None):
        if path == "/credential":
            return {
                "credential": {
                    "websocketUrl": "wss://api.x.ai/v1/realtime",
                    "model": "fixture",
                    "clientSecret": "PRIVATE_FIXTURE",
                    "expiresAtUnixSeconds": time.time() + 60,
                }
            }
        sessions.append(payload)
        return {"instructions": "fixture", "tools": []}

    async def ws_connect(*args, **kwargs):
        return sockets.pop(0)

    monkeypatch.setattr(GbotVoiceBridge, "_local", local)
    monkeypatch.setattr(
        bridge_module.aiohttp,
        "ClientSession",
        lambda **kwargs: SimpleNamespace(ws_connect=ws_connect, close=AsyncMock(), closed=False),
    )
    kws = FakeKws()
    proxy = GbotVoiceProxy(
        codec_factory=Codec,
        edge_tts_provider_factory=lambda **kwargs: SimpleNamespace(abort=AsyncMock()),
        wake_gate_factory=lambda: WakeGate(kws=kws),
    )
    proxy.wake_confirm_hold_s = 0
    leds = []

    async def set_leds(*rgb):
        leds.append(tuple(rgb))

    proxy._set_leds_safe = set_leds
    proxy._send_device_emotion_safe = AsyncMock()
    proxy._signal_wake_chime = lambda: None
    try:
        assert await proxy.start(SimpleNamespace(device_id="fixture"), {}, AsyncMock())
        first = proxy._bridge
        first_ws.release.set()  # ... 900 s of silence later
        for _ in range(100):
            if not first.running:
                break
            await asyncio.sleep(0.01)
        assert not first.running and not proxy.connected
        assert len(sessions) == 1
        assert DEAD_LED_RGB not in leds

        kws.fire = True
        await proxy._forward_gated_device_pcm(FRAME)
        await asyncio.wait_for(proxy._reopen_task, 2)
        await proxy._forward_gated_device_pcm(FRAME)
        assert len(sessions) == 2
        assert proxy.connected and proxy._bridge is not first
        assert first_ws.closed
        appended = [e for e in second_ws.sent if e.get("type") == "input_audio_buffer.append"]
        assert len(appended) == 2  # waking frame + follow-up frame, both upstream
        assert not [e for e in first_ws.sent if e.get("type") == "input_audio_buffer.append"]
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_child_failed_reopen_plays_packed_offline_tip(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    proxy = GbotVoiceProxy()
    send = AsyncMock()
    proxy._send_to_device = send
    proxy._set_leds_safe = AsyncMock()
    bridge = FakeBridge()
    bridge.fail = True
    monkeypatch.setattr(proxy, "_new_bridge", lambda: bridge)
    assert not await proxy._reopen_session("test")
    messages = [json.loads(call.args[0]) for call in send.await_args_list]
    assert messages == [{"type":"xiaoya", "cmd":"offline_tip", "clip":"vo.offline.wake"}]



@pytest.mark.asyncio
async def test_offline_tip_child_only_and_transport_failure(monkeypatch):
    proxy = GbotVoiceProxy()
    proxy._send_to_device = AsyncMock()
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "0")
    await proxy._send_offline_tip()
    proxy._send_to_device.assert_not_awaited()
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    proxy._send_to_device.side_effect = OSError("disconnected")
    await proxy._send_offline_tip()  # Cannot prevent the normal failure cleanup.
