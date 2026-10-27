import pytest

from stackchan_mcp.camera_presence import CameraPresence


def test_default_off_and_session_start():
    now = [0.0]
    policy = CameraPresence(clock=lambda: now[0])
    assert not policy.is_feature_on()
    now[0] = 200
    assert policy.tick() is None
    assert policy.note_face() is None
    policy.start_homework_session()
    assert policy.is_feature_on()


def test_leave_once_return_rotation_and_short_absence():
    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    now[0] = 10
    p.note_face()
    now[0] = 129.9
    assert p.tick() is None
    assert p.note_face() is None
    now[0] = 249.9
    assert p.tick().clip == "vo.leave_wait.1"
    now[0] = 900
    assert p.tick() is None
    assert p.note_face().clip == "vo.welcome_back.1"
    assert p.note_face() is None
    now[0] = 1020
    assert p.note_face().clip == "vo.welcome_back.2"


def test_manual_overrides_day_done_and_icon_transitions():
    from stackchan_mcp.camera_presence import presence_message

    p = CameraPresence()
    assert p.drain_messages() == []
    p.start_homework_session()
    p.start_homework_session()
    assert p.drain_messages() == [presence_message(True)]
    p.on_parent_setting(False)
    p.start_homework_session()
    assert not p.is_feature_on()
    assert p.drain_messages() == [presence_message(False)]
    p.on_parent_setting(True)
    assert p.is_feature_on()
    p.end_homework_day()
    assert not p.is_feature_on()
    p.on_parent_setting(True)
    assert p.is_feature_on()


@pytest.mark.parametrize("reason", ["photo", "evolve", "minigame", "speaking", "intro"])
def test_suppression_pauses_absence_and_drops_welcome(reason):
    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    now[0] = 10
    p.note_face()
    now[0] = 130
    assert p.tick().kind == "leave_tip"
    p.set_suppress(reason, True)
    now[0] = 1000
    assert p.tick() is None
    assert p.note_face() is None
    p.set_suppress(reason, False)
    assert p.note_face() is None
    now[0] = 1119
    assert p.tick() is None
    now[0] = 1120
    assert p.tick().kind == "leave_tip"


def test_grace_and_redo_once():
    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    now[0] = 9.9
    assert p.tick() is None
    assert p.note_face() is None
    p.set_pending_redo("爸爸想你再試一次。")
    now[0] = 130
    assert p.tick().kind == "leave_tip"
    action = p.note_face()
    assert action.kind == "redo"
    assert action.spoken == "爸爸想你再試一次。"
    assert p.note_face() is None
    now[0] = 250
    assert p.note_face().kind == "welcome"


def test_stored_preference_and_frames_contract():
    import inspect
    from stackchan_mcp import camera_presence
    from stackchan_mcp.parent_settings import SCHEMA

    assert SCHEMA["camera_presence"]["default"] is False
    assert CameraPresence(
        settings_getter=lambda: {"camera_presence": True}
    ).is_feature_on()
    assert "detection frames never saved/uploaded" in camera_presence.__doc__
    source = inspect.getsource(camera_presence)
    assert all(
        token not in source
        for token in ("open(", "write_bytes(", "requests.", "httpx.", "cv2.")
    )


def test_parent_explicit_off_when_stored_default_already_false(tmp_path):
    from stackchan_mcp.parent_settings import ParentSettings

    settings = ParentSettings(tmp_path / "settings.json")
    p = CameraPresence(settings_getter=settings.get)
    settings.add_listener(
        lambda values, keys: (
            p.on_parent_setting(values["camera_presence"])
            if "camera_presence" in keys
            else None
        )
    )
    p.start_homework_session()
    settings.set("camera_presence", False, source="parent")
    assert not p.is_feature_on()


@pytest.mark.parametrize("reason", ["photo", "evolve", "intro"])
def test_board_lifecycle_suppresses_policy(reason):
    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    p.note_device_event(
        {"event": "presence_activity", "reason": reason, "active": True}
    )
    now[0] = 200
    assert p.tick() is None
    p.note_device_event(
        {"event": "presence_activity", "reason": reason, "active": False}
    )
    now[0] = 319
    assert p.tick() is None
    now[0] = 320
    assert p.tick().kind == "leave_tip"


@pytest.mark.asyncio
async def test_homework_voice_entry_and_completion(tmp_path, monkeypatch):
    from stackchan_mcp import camera_presence, xiaoya_forms
    from stackchan_mcp.voice_tools import VoiceToolExecutor

    p = CameraPresence()
    forms = xiaoya_forms.XiaoyaForms(tmp_path / "forms.json")
    monkeypatch.setattr(camera_presence, "_presence", p)
    monkeypatch.setattr(xiaoya_forms, "_forms", forms)
    forms.add_items(["中文", "英文"], source="parent")
    VoiceToolExecutor._dispatch_homework("homework_list", {})
    assert p.is_feature_on()
    VoiceToolExecutor._dispatch_homework("homework_done", {"item": "中文"})
    from stackchan_mcp.homework_photo import get_homework_photo
    photo = get_homework_photo()
    photo.send = lambda *args: None
    _completed = photo.complete(False)
    assert p.is_feature_on()
    VoiceToolExecutor._dispatch_homework("homework_done", {"item": "英文"})
    from stackchan_mcp.homework_photo import get_homework_photo
    photo = get_homework_photo()
    photo.send = lambda *args: None
    _completed = photo.complete(False)
    assert not p.is_feature_on()
    p.on_parent_setting(True)
    VoiceToolExecutor._dispatch_homework("homework_list", {})
    assert p.is_feature_on()  # Completed list does not undo dad's later manual ON.


@pytest.mark.asyncio
async def test_track_and_idle_poll_deliver_local_lines(tmp_path, monkeypatch):
    from stackchan_mcp import camera_presence, xiaoya_forms, xiaoya_games
    from stackchan_mcp.gateway import Gateway

    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    forms = xiaoya_forms.XiaoyaForms(tmp_path / "forms.json")
    games = xiaoya_games.XiaoyaGames(tmp_path / "games.json")
    monkeypatch.setattr(camera_presence, "_presence", p)
    monkeypatch.setattr(xiaoya_forms, "_forms", forms)
    monkeypatch.setattr(xiaoya_games, "_games", games)
    messages = []

    async def sender(message):
        messages.append(message)

    class FaceRequest:
        async def json(self):
            return {"x": 0.5, "y": 0.5, "confidence": 0.9}

    task = forms.register_sender(sender)
    if task:
        await task
    gw = Gateway()
    try:
        p.start_homework_session()
        now[0] = 10
        assert (await gw._handle_track(FaceRequest())).status == 200
        now[0] = 130
        await gw.idle_gate.evaluate_once()
        assert any(m.get("clip") == "vo.leave_wait.1" for m in messages)
        await gw._handle_track(FaceRequest())
        await gw.idle_gate.evaluate_once()
        lines = [m for m in messages if m.get("cmd") == "presence_line"]
        assert [m["kind"] for m in lines] == ["leave_tip", "welcome"]
        assert lines[1]["clip"] == "vo.welcome_back.1"
    finally:
        forms.unregister_sender(sender)


@pytest.mark.parametrize("confidence", [None, True, "0.7", -1, 0.49, 1.01, float("nan"), float("inf")])
def test_device_presence_rejects_bad_confidence(confidence):
    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    now[0] = 121
    p.note_device_event({"type": "xiaoya", "event": "presence_hit", "confidence": confidence})
    assert p.tick().kind == "leave_tip"
    assert p.drain_actions() == []


def test_device_presence_hits_are_metadata_only_and_queue_return():
    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    now[0] = 121
    assert p.tick().kind == "leave_tip"
    hit = {"type": "xiaoya", "event": "presence_hit", "confidence": 0.7}
    p.note_device_event({**hit, "data": "image forbidden"})
    assert p.drain_actions() == []
    p.note_device_event(hit)
    assert p.drain_actions()[0].kind == "welcome"
    assert p.drain_actions() == []
    now[0] = 200
    p.note_device_event(hit)
    now[0] = 310
    assert p.tick() is None


def test_disabled_detector_suppresses_false_leave_until_hits_available():
    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    p.note_device_event({"event": "presence_status", "running": True, "hits_enabled": False})
    now[0] = 500
    assert p.tick() is None
    p.note_device_event({"event": "presence_status", "running": True, "hits_enabled": True})
    assert not p.is_suppressed()
    now[0] = 619
    assert p.tick() is None
    now[0] = 620
    assert p.tick().kind == "leave_tip"


@pytest.mark.parametrize("clips", [("a", "b"), ("a", "b", "c"), tuple("abcdefg")])
def test_clip_pool_recent_window(clips):
    from stackchan_mcp.camera_presence import ClipPool

    pool = ClipPool(clips)
    history = []
    for _ in range(40):
        clip = pool.next()
        assert clip not in history[-min(5, len(clips) - 1):]
        history.append(clip)


def test_single_joke_and_exhausted_pool():
    from stackchan_mcp.camera_presence import ClipPool, JOKE_CLIPS, WELCOME_CLIPS

    assert WELCOME_CLIPS == ("vo.welcome_back.1", "vo.welcome_back.2")
    assert JOKE_CLIPS == ("vo.joke.1",)
    for clips in (JOKE_CLIPS, ("vo.joke.1",) * 3):
        pool = ClipPool(clips)
        assert [pool.next() for _ in range(30)] == ["vo.joke.1"] * 30
