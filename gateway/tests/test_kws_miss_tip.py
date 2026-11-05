from datetime import date

import pytest

from stackchan_mcp.kws_miss_tip import KwsMissTip, PARENT_HELPER_BOT, TIP_TEXT
from stackchan_mcp.debug_status import DebugStatus


def test_daily_tip_persists_and_resets(tmp_path):
    day = [date(2026, 10, 27)]
    sent = []
    kwargs = dict(
        path=tmp_path / "state.json",
        today=lambda: day[0],
        send=lambda *args: sent.append(args),
    )
    tip = KwsMissTip(**kwargs)
    tip.note_boot_listen()
    tip.note_boot_listen()
    assert not sent
    tip.note_boot_listen()
    tip.note_boot_listen()
    assert sent == [(PARENT_HELPER_BOT, TIP_TEXT)]
    restarted = KwsMissTip(**kwargs)
    restarted.note_boot_listen()
    assert len(sent) == 1
    day[0] = date(2026, 10, 28)
    for _ in range(3):
        restarted.note_boot_listen()
    assert len(sent) == 2


def test_wake_hit_prevents_tip_and_status_mirrors_counts(tmp_path):
    sent = []
    tip = KwsMissTip(tmp_path / "state.json", send=lambda *args: sent.append(args))
    status = DebugStatus(kws_miss_tip=tip)
    status.on_wake_word_hit()
    for _ in range(4):
        status.on_boot_listen()
    snap = status.snapshot()["wake_gate"]
    assert snap["wake_word_count"] == 1
    assert snap["boot_listen_count"] == 4
    assert snap["wake_count"] == 0
    assert not sent


def test_send_failure_does_not_raise_or_repeat(tmp_path, caplog):
    def fail(*args):
        raise RuntimeError("offline")

    tip = KwsMissTip(tmp_path / "state.json", send=fail)
    for _ in range(4):
        tip.note_boot_listen()
    assert tip.counts()["tipped"]
    assert "parent tip send failed" in caplog.text


def test_connected_backup_requires_active_wake_and_throttles(tmp_path):
    now = [7200]
    sent = []
    tip = KwsMissTip(
        tmp_path / "state.json",
        clock=lambda: now[0],
        send=lambda *args: sent.append(args),
    )
    kwargs = dict(
        device_connected=True,
        connected_since=0,
        listening_mode="pass_through",
        wake_available=True,
    )
    tip.maybe_check_connected(**kwargs)
    kwargs["listening_mode"] = "wake_word"
    tip.maybe_check_connected(**kwargs)
    assert not sent
    now[0] += 60
    tip.maybe_check_connected(**kwargs)
    tip.maybe_check_connected(**kwargs)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_send_is_background_and_reserved_before_dispatch(tmp_path, monkeypatch):
    import asyncio

    dispatched = []
    sent = []

    async def fake_to_thread(fn):
        dispatched.append(fn)
        fn()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    tip = KwsMissTip(tmp_path / "state.json", send=lambda *args: sent.append(args))
    for _ in range(4):
        tip.note_boot_listen()
    assert tip.counts()["tipped"]
    assert not sent
    await asyncio.gather(*tip._tasks)
    assert len(dispatched) == 1
    assert sent == [(PARENT_HELPER_BOT, TIP_TEXT)]
