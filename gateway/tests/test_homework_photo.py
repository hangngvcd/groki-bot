import os

import pytest

from stackchan_mcp.camera_presence import CameraPresence
from stackchan_mcp.homework_photo import HomeworkPhoto, resolve_item, assess_photo
from stackchan_mcp.stuck_notify import PARENT_HELPER_BOT
from stackchan_mcp.xiaoya_forms import XiaoyaForms


@pytest.fixture
def flow(tmp_path, monkeypatch):
    from stackchan_mcp import camera_presence, xiaoya_games
    from stackchan_mcp.xiaoya_games import XiaoyaGames

    presence = CameraPresence()
    monkeypatch.setattr(camera_presence, "_presence", presence)
    monkeypatch.setattr(xiaoya_games, "_games", XiaoyaGames(tmp_path / "games.json"))
    forms = XiaoyaForms(tmp_path / "forms.json", today=lambda: "2026-10-27")
    forms.add_items(["中文", "數學"], source="test")
    sent = []
    photo = HomeworkPhoto(
        forms=forms,
        presence=presence,
        capture=lambda: b"jpeg",
        assess=lambda path: "ok",
        send=lambda *args: sent.append(args),
        captures_dir=tmp_path / "captures",
    )
    return photo, forms, presence, sent


def test_resolve():
    assert resolve_item(None, ["中文"])["item"] == "中文"
    assert resolve_item("數學", ["中文", "數學"])["item"] == "數學"
    result = resolve_item(None, ["一", "二", "三", "四"])
    assert result["spoken"] == "係邊樣做完？" and len(result["choices"]) == 3
    assert resolve_item(None, [])["status"] == "none"


def test_ask_once(flow):
    photo, *_ = flow
    assert photo.resolve()["spoken"]
    assert photo.resolve()["spoken"] == ""
    assert photo.resolve("中文")["photo_pending"]


def test_success_unlock_and_payload(flow):
    photo, forms, presence, sent = flow
    photo.resolve("#1")
    assert forms.snapshot()["done"] == 0
    result = photo.attempt_capture()
    assert result["photo_sent"] and result["game_unlock"] and result["level_up"]
    assert result["invite_spoken"] and result["local_game_handoff"]
    assert sent[0][0] == PARENT_HELPER_BOT
    assert "中文" in sent[0][1] and str(photo.captures_dir) in sent[0][1]
    assert "對話" not in sent[0][1]
    assert [m["phase"] for m in result["device_messages"]] == ["shutter", "done"]
    assert not presence._suppress


@pytest.mark.parametrize("quality", ["dark", "blank", "misalign"])
def test_two_retries_then_no_photo(flow, quality):
    photo, forms, presence, sent = flow
    photo.assess = lambda path: quality
    photo.resolve("中文")
    for attempt in (1, 2):
        result = photo.attempt_capture()
        assert result["photo_pending"] and result["attempts"] == attempt
        assert forms.snapshot()["done"] == 0
    result = photo.attempt_capture()
    assert result["no_photo"] and result["parent_notified"]
    assert forms.snapshot()["done"] == 1 and "冇相" in sent[0][1]
    assert not presence._suppress
    assert not photo.attempt_capture()["ok"]


def test_cancel_and_suppress(flow):
    photo, forms, presence, sent = flow
    now = [0.0]
    presence.clock = lambda: now[0]
    presence.start_homework_session()
    now[0] = 200
    photo.resolve("中文")
    assert presence.tick() is None
    assert presence.note_face() is None
    assert photo.cancel()["device_messages"][0]["phase"] == "cancel"
    assert not presence._suppress and forms.snapshot()["done"] == 0 and not sent
    now[0] += 121
    assert presence.tick().kind == "leave_tip"


@pytest.mark.parametrize("reason", ["", "再睇第二題", "懶亂做"])
def test_redo_once(flow, reason):
    photo, forms, presence, _ = flow
    forms.mark_done("中文", source="test")
    forms.redo_items(["中文"], source="parent", reason=reason)
    line = presence.consume_pending_redo().spoken
    assert "中文" in line and "懶" not in line and "亂做" not in line
    if reason == "再睇第二題":
        assert reason in line
    assert presence.consume_pending_redo() is None
    assert forms.snapshot()["form"] == 2


def test_cleanup_and_quality(flow):
    photo, *_ = flow
    path = photo.captures_dir / "homework_old.jpg"
    path.parent.mkdir()
    path.write_bytes(b"")
    os.utime(path, (0, 0))
    assert assess_photo(path) == "blank"
    photo.cleanup()
    assert not path.exists()


def test_send_failure_still_completes(flow):
    photo, forms, presence, _ = flow

    def fail(*args):
        raise OSError("offline")

    photo.send = fail
    photo.resolve("中文")
    result = photo.attempt_capture()
    assert not result["photo_sent"] and not result["parent_notified"]
    assert forms.snapshot()["done"] == 1 and not presence._suppress
