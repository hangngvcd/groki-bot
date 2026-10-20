# SPDX-License-Identifier: MIT
"""Privacy contracts through the shared voice proxy injection points."""
import logging

import pytest

from stackchan_mcp.cloud_proxy import CloudConnectionInfo
from stackchan_mcp.debug_status import DebugStatus
from stackchan_mcp.gbot_voice_proxy import GbotVoiceProxy
from stackchan_mcp.gemini_voice_proxy import GeminiVoiceProxy
from stackchan_mcp.wake_gate import create_wake_gate_from_env
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
@pytest.mark.parametrize("source", ["disabled", "missing", "keyword", "failed"])
async def test_privacy_window(monkeypatch, caplog, proxy_type, source):
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
        await proxy.send_device_json({"type": "listen", "state": "start"})
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
        await proxy.send_device_json({"type": "listen", "state": "detect"})
        await proxy.send_device_binary(speech)
        assert bridge.audio_sent == [speech, silence, speech]
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_default_without_model_still_passes_through(monkeypatch):
    monkeypatch.delenv("STACKCHAN_BUTTON_ONLY_LISTEN", raising=False)
    monkeypatch.setenv("STACKCHAN_WAKE_WORD", "0")
    bridge = FakeBridge()
    proxy = GbotVoiceProxy(
        bridge_factory=lambda *args, **kwargs: bridge,
        codec_factory=PCMCodec,
        edge_tts_provider_factory=FakeEdgeTTSProvider,
    )

    async def send(message):
        pass

    try:
        info = CloudConnectionInfo("wss://unused.invalid", "", "1", "device", "client")
        assert await proxy.start(info, {}, send)
        await proxy.send_device_binary(_pcm(1200))
        assert bridge.audio_sent == [_pcm(1200)]
    finally:
        await proxy.stop()
