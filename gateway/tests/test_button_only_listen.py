# SPDX-License-Identifier: MIT
"""Privacy contracts through the shared voice proxy injection points."""
import logging

import pytest

from stackchan_mcp.cloud_proxy import CloudConnectionInfo
from stackchan_mcp.debug_status import DebugStatus
from stackchan_mcp.gbot_voice_proxy import GbotVoiceProxy
from stackchan_mcp.gemini_voice_proxy import GeminiVoiceProxy
from stackchan_mcp.wake_gate import create_wake_gate_from_env, is_explicit_listen_request
from test_gemini_voice_proxy import (
    FakeBridge,
    FakeCodec,
    FakeEdgeTTSProvider,
    FakeVoiceOnlyESP32,
)
from test_wake_gate import FakeClock, FakeKeywordSpotter, _pcm


class PCMCodec(FakeCodec):
    def decode_device_frame(self, frame):
        return frame


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_type", [GbotVoiceProxy, GeminiVoiceProxy])
@pytest.mark.parametrize("listen_message", [
    {"type": "listen", "state": "detect", "source": "button"},
    {"type": "listen", "state": "start", "mode": "manual"},
])
@pytest.mark.parametrize("source", ["disabled", "missing", "keyword", "failed"])
async def test_privacy_window(monkeypatch, caplog, proxy_type, source, listen_message):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("STACKCHAN_BUTTON_ONLY_LISTEN", "1")
    monkeypatch.setenv("STACKCHAN_WAKE_WORD", "0" if source == "disabled" else "1")
    monkeypatch.setenv("STACKCHAN_KWS_MODEL_DIR", "/nonexistent/button-only-test")
    monkeypatch.setenv("STACKCHAN_WAKE_IDLE_S", "3")
    clock = FakeClock()
    spotter = FakeKeywordSpotter([False, True]) if source == "keyword" else None
    if source == "failed":
        class BrokenSpotter(FakeKeywordSpotter):
            def detect(self, pcm):
                raise RuntimeError("detector failed")
        spotter = BrokenSpotter()
    bridge = FakeBridge()
    device = FakeVoiceOnlyESP32()
    status = DebugStatus()
    proxy = proxy_type(
        esp32_ref=lambda: device,
        bridge_factory=lambda *args, **kwargs: bridge,
        codec_factory=PCMCodec,
        edge_tts_provider_factory=FakeEdgeTTSProvider,
        wake_gate_factory=lambda: create_wake_gate_from_env(kws=spotter, clock=clock),
        debug_status=status,
        wake_confirm_hold_s=0,
    )
    info = CloudConnectionInfo("wss://unused.invalid", "", "1", "device", "client")
    caplog.set_level(logging.INFO)

    async def send(message):
        pass

    try:
        assert await proxy.start(info, {}, send)
        mode = "wake_word_or_button" if spotter else "button_only"
        assert status.snapshot()["wake_gate"]["listening_mode"] == mode
        assert f"Gateway listening mode: {mode}" in caplog.text
        before, speech, silence = _pcm(100), _pcm(1200), _pcm(0)
        await proxy.send_device_binary(before)
        assert bridge.audio_sent == []
        if source == "keyword":
            await proxy.send_device_binary(speech)
            assert bridge.audio_sent == [before, speech]
            clock.advance(3)
            await proxy.send_device_binary(silence)
            assert bridge.audio_sent == [before, speech]
            bridge.audio_sent.clear()
        await proxy.send_device_json({"type": "listen", "state": "stop"})
        await proxy.send_device_binary(before)
        assert bridge.audio_sent == []
        await proxy.send_device_json(listen_message)
        await proxy.send_device_binary(speech)
        assert bridge.audio_sent == [speech]
        assert (0, 180, 180) in device.leds
        clock.advance(2)
        await proxy.send_device_binary(silence)
        assert bridge.audio_sent == [speech, silence]
        clock.advance(1)
        await proxy.send_device_binary(silence)
        await proxy.send_device_binary(speech)
        assert bridge.audio_sent == [speech, silence]
        assert device.leds[-1] == (0, 0, 0)
        await proxy.send_device_json(listen_message)
        await proxy.send_device_binary(speech)
        assert bridge.audio_sent == [speech, silence, speech]
    finally:
        await proxy.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_type", [GbotVoiceProxy, GeminiVoiceProxy])
async def test_default_without_model_still_passes_through(monkeypatch, proxy_type):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("STACKCHAN_BUTTON_ONLY_LISTEN", raising=False)
    monkeypatch.setenv("STACKCHAN_WAKE_WORD", "0")
    bridge = FakeBridge()
    device = FakeVoiceOnlyESP32()
    proxy = proxy_type(
        esp32_ref=lambda: device,
        bridge_factory=lambda *args, **kwargs: bridge,
        codec_factory=PCMCodec,
        edge_tts_provider_factory=FakeEdgeTTSProvider,
    )

    async def send(message):
        pass

    try:
        info = CloudConnectionInfo("wss://unused.invalid", "", "1", "device", "client")
        assert await proxy.start(info, {}, send)
        await proxy.send_device_json({"type": "listen", "state": "start", "mode": "auto"})
        await proxy.send_device_binary(_pcm(1200))
        assert bridge.audio_sent == [_pcm(1200)]
    finally:
        await proxy.stop()


NON_EXPLICIT_REQUESTS = [
    {"type": "listen", "state": "start", "mode": "auto"},
    {"type": "listen", "state": "start", "mode": "realtime"},
    {"type": "listen", "state": "start"},
    {"type": "listen", "state": "detect"},
    {"type": "listen", "state": "detect", "source": "wake_word"},
]


@pytest.mark.parametrize("message, expected", [
    *[(message, False) for message in NON_EXPLICIT_REQUESTS],
    ({}, False),
    ({"type": "listen", "state": "stop", "mode": "manual", "source": "button"}, False),
    ({"type": "other", "state": "detect", "source": "button"}, False),
    ({"type": None, "state": 1, "source": ["button"]}, False),
    ({"type": "listen", "state": "detect", "source": None}, False),
    ({"type": "listen", "state": "start", "mode": None}, False),
    ({"type": "listen", "state": "detect", "source": "button"}, True),
    ({"type": "listen", "state": "start", "mode": "manual"}, True),
    ({"type": " LISTEN ", "state": " DeTeCt ", "source": " BUTTON "}, True),
    ({"type": " LISTEN ", "state": " START ", "mode": " Manual "}, True),
])
def test_is_explicit_listen_request(message, expected):
    assert is_explicit_listen_request(message) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_type", [GbotVoiceProxy, GeminiVoiceProxy])
@pytest.mark.parametrize("message", NON_EXPLICIT_REQUESTS)
@pytest.mark.parametrize("reconnect", [False, True])
async def test_implicit_listen_stays_dormant(monkeypatch, proxy_type, message, reconnect):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("STACKCHAN_BUTTON_ONLY_LISTEN", "1")
    monkeypatch.setenv("STACKCHAN_WAKE_WORD", "0")
    bridge = FakeBridge()
    status = DebugStatus()
    # Cover a factory that caches and reuses its gate across sessions.
    gate = create_wake_gate_from_env(clock=FakeClock())
    device = FakeVoiceOnlyESP32()
    proxy = proxy_type(
        esp32_ref=lambda: device,
        bridge_factory=lambda *args, **kwargs: bridge,
        codec_factory=PCMCodec,
        edge_tts_provider_factory=FakeEdgeTTSProvider,
        wake_gate_factory=lambda: gate,
        debug_status=status,
        wake_confirm_hold_s=0,
    )
    info = CloudConnectionInfo("wss://unused.invalid", "", "1", "device", "client")

    async def send(message):
        pass

    try:
        assert await proxy.start(info, {}, send)
        if reconnect:
            await proxy.send_device_json({"type": "listen", "state": "detect", "source": "button"})
            await proxy.send_device_binary(_pcm(1200))
            assert bridge.audio_sent == [_pcm(1200)]
            await proxy.stop()
            bridge.audio_sent.clear()
            assert await proxy.start(info, {}, send)
        wake_count = status.snapshot()["wake_gate"]["wake_count"]
        assert wake_count == (1 if reconnect else 0)
        await proxy.send_device_json(message)
        await proxy.send_device_binary(_pcm(1200))
        assert bridge.audio_sent == []
        snapshot = status.snapshot()["wake_gate"]
        assert snapshot["state"] == "DORMANT"
        assert snapshot["wake_count"] == wake_count
    finally:
        await proxy.stop()
