"""鈴鈴 must not wake herself: the wake word is ignored while she speaks and 0.5 s after."""

from __future__ import annotations

import struct

from stackchan_mcp.wake_gate import WakeGate, WakeGateState


class Spotter:
    available = True

    def __init__(self) -> None:
        self.fed = 0
        self.resets = 0

    def detect(self, pcm: bytes) -> bool:
        self.fed += 1
        return True  # every frame "contains" 鈴鈴

    def reset(self) -> None:
        self.resets += 1


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _frame() -> bytes:
    return struct.pack("<160h", *([3000] * 160))


def _gate(tts: dict, **kw) -> tuple[WakeGate, Spotter, Clock]:
    clock = Clock()
    kws = Spotter()
    gate = WakeGate(kws=kws, clock=clock, is_tts_active=lambda: tts["on"], **kw)
    return gate, kws, clock


def test_wake_word_ignored_while_tts_active():
    tts = {"on": True}
    gate, kws, clock = _gate(tts)
    for _ in range(10):
        r = gate.process(_frame())
        clock.now += 0.04
        assert r.muted and not r.woke and r.forward_pcm == ()
    assert kws.fed == 0
    assert gate.state == WakeGateState.DORMANT


def test_tail_starts_at_first_frame_after_tts_end():
    tts = {"on": True}
    gate, kws, clock = _gate(tts)
    gate.process(_frame())
    tts["on"] = False
    # Half-duplex device: no frames for 3 s while the speaker drains.
    clock.now += 3.0
    assert gate.process(_frame()).muted  # t = 0
    clock.now += 0.25
    assert gate.process(_frame()).muted  # t = 0.25
    clock.now += 0.24
    assert gate.process(_frame()).muted  # t = 0.49
    clock.now += 0.02
    r = gate.process(_frame())  # t = 0.51
    assert r.woke and not r.muted
    assert kws.fed == 1
    assert kws.resets >= 1  # stale hypothesis dropped when the mute ends


def test_tail_preroll_does_not_contain_own_voice():
    tts = {"on": True}
    gate, kws, clock = _gate(tts)
    own = struct.pack("<160h", *([1234] * 160))
    gate.process(own)
    tts["on"] = False
    gate.process(own)
    clock.now += 0.6
    child = _frame()
    r = gate.process(child)
    assert r.woke and r.forward_pcm == (child,)


def test_device_local_speech_mutes_and_has_tail():
    tts = {"on": False}
    gate, kws, clock = _gate(tts)
    gate.set_local_speech(True)
    assert gate.self_muted
    assert gate.process(_frame()).muted
    clock.now += 5
    gate.set_local_speech(False)
    assert gate.self_muted  # tail still pending
    assert gate.process(_frame()).muted
    clock.now += 0.5
    assert gate.process(_frame()).woke


def test_stuck_local_speech_flag_clears():
    gate, kws, clock = _gate({"on": False}, local_speech_max_s=10)
    gate.set_local_speech(True)
    assert gate.process(_frame()).muted
    clock.now += 11
    gate.process(_frame())  # flag expires -> tail
    clock.now += 0.6
    assert gate.process(_frame()).woke


def test_tail_can_be_disabled():
    tts = {"on": True}
    gate, kws, clock = _gate(tts, self_mute_tail_s=-1)
    assert gate.process(_frame()).woke  # legacy behaviour: no muting at all


def test_no_mute_without_any_speech():
    gate, kws, clock = _gate({"on": False})
    assert gate.process(_frame()).woke


def test_env_tail(monkeypatch):
    from stackchan_mcp.wake_gate import create_wake_gate_from_env

    monkeypatch.setenv("STACKCHAN_WAKE_SELF_MUTE_TAIL_S", "0.8")
    gate = create_wake_gate_from_env(kws=Spotter())
    assert gate is not None and gate.self_mute_tail_s == 0.8
