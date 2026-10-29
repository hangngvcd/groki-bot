# SPDX-License-Identifier: MIT
"""Ticket 32: stuck ×3 → consent → parent-helper notify (at most once/item/day)."""

from datetime import date, datetime

import pytest

from stackchan_mcp.stuck_notify import (
    EMPATHY_LINE,
    OFFER_LINE,
    PARENT_HELPER_BOT,
    SPOKEN_FAIL,
    SPOKEN_OK,
    StuckNotify,
    format_parent_tip,
)
from stackchan_mcp.voice_tools import VoiceToolExecutor


def _stuck(tmp_path, *, day=None, sent=None, send=None):
    day = day or [date(2026, 10, 27)]
    sent = sent if sent is not None else []
    return StuckNotify(
        path=tmp_path / "stuck.json",
        today=lambda: day[0],
        send=send or (lambda *args: sent.append(args)),
    ), day, sent


def test_no_notify_without_consent(tmp_path):
    stuck, _, sent = _stuck(tmp_path)
    for _ in range(3):
        stuck.note_stuck("數學練習", "第3題")
    result = stuck.notify_dad("數學練習", "第3題", consented=False)
    assert result["ok"] is False
    assert result["error"] == "consent_required"
    assert not sent
    assert "爸爸收到" not in result["spoken"]


def test_offer_after_three_stucks_then_notify_once(tmp_path):
    stuck, _, sent = _stuck(tmp_path)
    assert stuck.note_stuck("英文讀寫", "vocabulary")["offer_help"] is False
    assert stuck.note_stuck("英文讀寫", "vocabulary")["offer_help"] is False
    offered = stuck.note_stuck("英文讀寫", "vocabulary")
    assert offered["offer_help"] is True
    assert offered["count"] == 3
    assert EMPATHY_LINE in offered["spoken"]
    assert OFFER_LINE in offered["spoken"]
    assert "蠢" not in offered["spoken"]

    # Same count again without give_up: do not nag.
    assert stuck.note_stuck("英文讀寫", "vocabulary")["offer_help"] is False

    ok = stuck.notify_dad("英文讀寫", "vocabulary", consented=True)
    assert ok == {"ok": True, "sent": True, "spoken": SPOKEN_OK}
    assert len(sent) == 1
    bot, tip = sent[0]
    assert bot == PARENT_HELPER_BOT
    assert "英文讀寫" in tip
    assert "vocabulary" in tip
    assert "【鈴鈴提示】" in tip
    assert SPOKEN_OK not in tip  # tip is parent-only wording family, ok either way
    assert "對話" not in tip

    again = stuck.notify_dad("英文讀寫", consented=True)
    assert again["already_notified"] is True
    assert again["sent"] is False
    assert again["spoken"] == SPOKEN_OK
    assert len(sent) == 1


def test_give_up_at_threshold_reoffers_without_extra_count(tmp_path):
    stuck, _, sent = _stuck(tmp_path)
    for _ in range(3):
        stuck.note_stuck("數學")
    assert stuck.snapshot("數學")["count"] == 3
    # Already offered at 3; give_up re-offers without bumping past needlessly... 
    # implementation increments only if count < threshold when give_up, so stays 3.
    result = stuck.note_stuck("數學", give_up=True)
    assert result["offer_help"] is True
    assert result["count"] == 3
    assert not sent


def test_new_item_resets_counter_and_allows_separate_notify(tmp_path):
    stuck, _, sent = _stuck(tmp_path)
    for _ in range(3):
        stuck.note_stuck("數學")
    stuck.notify_dad("數學", consented=True)
    assert len(sent) == 1

    for _ in range(2):
        assert stuck.note_stuck("中文抄寫")["offer_help"] is False
    offered = stuck.note_stuck("中文抄寫")
    assert offered["offer_help"] is True
    assert offered["count"] == 3
    stuck.notify_dad("中文抄寫", consented=True)
    assert len(sent) == 2


def test_new_point_on_same_item_resets_count(tmp_path):
    stuck, _, _ = _stuck(tmp_path)
    stuck.note_stuck("數學", "第1題")
    stuck.note_stuck("數學", "第1題")
    assert stuck.snapshot("數學")["count"] == 2
    again = stuck.note_stuck("數學", "第2題")
    assert again["count"] == 1
    assert again["offer_help"] is False


def test_day_rollover_resets(tmp_path):
    stuck, day, sent = _stuck(tmp_path)
    for _ in range(3):
        stuck.note_stuck("數學")
    stuck.notify_dad("數學", consented=True)
    assert len(sent) == 1
    day[0] = date(2026, 10, 28)
    assert stuck.snapshot("數學")["count"] == 0
    assert stuck.snapshot("數學")["notified"] is False
    for _ in range(3):
        stuck.note_stuck("數學")
    stuck.notify_dad("數學", consented=True)
    assert len(sent) == 2


def test_send_failure_spoken_fail_not_received(tmp_path):
    def fail(*_args):
        raise RuntimeError("offline")

    stuck, _, _ = _stuck(tmp_path, send=fail)
    for _ in range(3):
        stuck.note_stuck("科學")
    result = stuck.notify_dad("科學", consented=True)
    assert result["ok"] is True
    assert result["sent"] is False
    assert result["spoken"] == SPOKEN_FAIL
    assert "爸爸收到" not in result["spoken"]
    # Failed delivery remains retryable and must never claim success.
    again = stuck.notify_dad("科學", consented=True)
    assert not again.get("already_notified")
    assert again["spoken"] == SPOKEN_FAIL
    assert stuck.snapshot("科學")["notified"] is False


def test_format_parent_tip_has_no_transcript_or_photo():
    tip = format_parent_tip(
        item="數學練習",
        stuck_point="應用題第2步",
        when=datetime(2026, 10, 27, 20, 15, tzinfo=datetime.now().astimezone().tzinfo),
    )
    assert "數學練習" in tip
    assert "應用題第2步" in tip
    assert "2026-10-27" in tip
    assert "transcript" not in tip.lower()
    assert "相" not in tip
    assert "對話全文" not in tip


def test_persist_across_restart(tmp_path):
    sent = []
    kwargs = dict(
        path=tmp_path / "stuck.json",
        today=lambda: date(2026, 10, 27),
        send=lambda *a: sent.append(a),
    )
    first = StuckNotify(**kwargs)
    for _ in range(3):
        first.note_stuck("數學")
    first.notify_dad("數學", consented=True)
    second = StuckNotify(**kwargs)
    assert second.snapshot("數學")["notified"] is True
    second.notify_dad("數學", consented=True)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_voice_tools_consent_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    sent = []
    stuck = StuckNotify(
        path=tmp_path / "stuck.json",
        today=lambda: date(2026, 10, 27),
        send=lambda *a: sent.append(a),
    )
    monkeypatch.setattr(
        "stackchan_mcp.stuck_notify.get_stuck_notify", lambda: stuck
    )
    executor = VoiceToolExecutor()
    try:
        names = {entry["name"] for entry in executor.declarations()}
        assert "note_homework_stuck" in names
        assert "notify_dad_stuck" in names
        assert "我話咗俾爸爸知" in executor.instructions()
        assert "notify_dad_stuck" in executor.instructions()

        for _ in range(3):
            result = await executor.execute(
                "note_homework_stuck", {"item": "數學練習", "point": "第3題"}
            )
        assert result["offer_help"] is True

        denied = await executor.execute(
            "notify_dad_stuck",
            {"item": "數學練習", "stuck_point": "第3題", "consented": False},
        )
        assert denied["ok"] is False
        assert not sent

        ok = await executor.execute(
            "notify_dad_stuck",
            {"item": "數學練習", "stuck_point": "第3題", "consented": True},
        )
        assert ok["spoken"] == SPOKEN_OK
        assert ok["sent"] is True
        assert len(sent) == 1
        # Child-facing result must not include the parent tip body.
        assert "【鈴鈴提示】" not in str(ok)
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_break_choice_does_not_notify(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    sent = []
    stuck = StuckNotify(
        path=tmp_path / "stuck.json",
        send=lambda *a: sent.append(a),
    )
    monkeypatch.setattr(
        "stackchan_mcp.stuck_notify.get_stuck_notify", lambda: stuck
    )
    executor = VoiceToolExecutor()
    try:
        for _ in range(3):
            await executor.execute("note_homework_stuck", {"item": "數學"})
        # She picks break: no notify_dad_stuck call → dad silent.
        assert not sent
        snap = stuck.snapshot("數學")
        assert snap["notified"] is False
        assert snap["count"] == 3
    finally:
        await executor.close()
