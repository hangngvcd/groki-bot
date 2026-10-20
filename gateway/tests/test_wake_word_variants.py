# SPDX-License-Identifier: MIT
"""Seam G: variant detections open the shared voice proxy's audio gate."""

import pytest

from stackchan_mcp.cloud_proxy import CloudConnectionInfo
from stackchan_mcp.gbot_voice_proxy import GbotVoiceProxy
from stackchan_mcp.wake_gate import (
    SherpaOnnxKeywordSpotter,
    XIAOBAI_WAKE_KEYWORD,
    create_wake_gate_from_env,
    parse_keyword_variants,
)
from test_button_only_listen import PCMCodec
from test_gemini_voice_proxy import FakeBridge, FakeEdgeTTSProvider, FakeVoiceOnlyESP32
from test_wake_gate import FakeClock, FakeKeywordSpotter, _pcm


class VariantSpotter(FakeKeywordSpotter):
    def __init__(self, result):
        super().__init__()
        self.result = result
        self.matcher = SherpaOnnxKeywordSpotter.__new__(SherpaOnnxKeywordSpotter)
        self.matcher._phrase = "小白"
        self.matcher._variants = parse_keyword_variants(XIAOBAI_WAKE_KEYWORD)

    def detect(self, pcm):
        return self.matcher._result_matches(self.result)


@pytest.mark.asyncio
@pytest.mark.parametrize("result,hit", [("小白小白", True), ("unrelated", False)])
async def test_variant_controls_forwarding(monkeypatch, result, hit):
    monkeypatch.setenv("STACKCHAN_WAKE_WORD", "1")
    monkeypatch.setenv("STACKCHAN_BUTTON_ONLY_LISTEN", "1")
    bridge = FakeBridge()
    device = FakeVoiceOnlyESP32()
    proxy = GbotVoiceProxy(
        esp32_ref=lambda: device,
        bridge_factory=lambda *args, **kwargs: bridge,
        codec_factory=PCMCodec,
        edge_tts_provider_factory=FakeEdgeTTSProvider,
        wake_gate_factory=lambda: create_wake_gate_from_env(
            kws=VariantSpotter(result), clock=FakeClock()
        ),
        wake_confirm_hold_s=0,
    )

    async def send(message):
        pass

    try:
        info = CloudConnectionInfo("wss://unused.invalid", "", "1", "device", "client")
        assert await proxy.start(info, {}, send)
        await proxy.send_device_binary(_pcm(1200))
        await proxy.send_device_binary(_pcm(1300))
        assert bridge.audio_sent == ([_pcm(1200), _pcm(1300)] if hit else [])
    finally:
        await proxy.stop()
