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

    assert WELCOME_CLIPS == tuple(f"vo.welcome_back.{i}" for i in range(1, 7))
    assert JOKE_CLIPS == tuple(f"vo.joke.{i}" for i in range(1, 4))
    for clips in (("vo.joke.1",), ("vo.joke.1",) * 3):
        pool = ClipPool(clips)
        assert [pool.next() for _ in range(30)] == ["vo.joke.1"] * 30


def test_full_packed_pools_and_recent_avoid():
    from stackchan_mcp.camera_presence import WELCOME_CLIPS, JOKE_CLIPS, PRAISE_CLIPS, ClipPool
    assert PRAISE_CLIPS == tuple(f"vo.praise.{i}" for i in range(1, 5))
    pool = ClipPool(WELCOME_CLIPS + JOKE_CLIPS)
    recent = []
    for _ in range(40):
        clip = pool.next()
        assert clip not in recent[-5:]
        recent.append(clip)
    now = [0.0]
    presence = CameraPresence(clock=lambda: now[0])
    presence.on_parent_setting(True)
    for _ in range(9):
        now[0] += 130
        action = presence.note_face()
        assert action.kind == ("joke" if action.clip in JOKE_CLIPS else "welcome")


def test_explicit_status():
    from stackchan_mcp.camera_presence import CameraPresence
    now = [0]
    presence = CameraPresence(clock=lambda: now[0])
    presence.on_parent_setting(True)
    now[0] = 20
    presence.note_device_event({'event':'presence_status','present':True,'confidence':0.9})
    assert presence._last_face == 20
    now[0] = 30
    for present, confidence in [(False,0.9),(True,0.4),(None,0.9),(True,float('nan')),(True,1.1)]:
        presence.note_device_event({'event':'presence_status','present':present,'confidence':confidence})
        assert presence._last_face == 20


@pytest.mark.parametrize("invalid", [None, True, "3", 0, 31, 2.5])
def test_invalid_leave_threshold_uses_default(invalid):
    now = [0]
    p = CameraPresence(clock=lambda: now[0], settings_getter=lambda: {
        "camera_presence": True, "presence_leave_min": invalid,
    })
    now[0] = 119
    assert p.tick() is None
    now[0] = 120
    assert p.tick().kind == "leave_tip"


def test_live_threshold_for_tick_and_note_face(tmp_path):
    from stackchan_mcp.parent_settings import ParentSettings, GATEWAY, TICKETS

    settings = ParentSettings(tmp_path / "settings.json")
    assert settings.get()["presence_leave_min"] == 2
    assert "presence_leave_min" in GATEWAY
    assert TICKETS["presence_leave_min"] == "23"
    now = [0]
    p = CameraPresence(clock=lambda: now[0], settings_getter=settings.get)
    p.on_parent_setting(True)
    settings.set("presence_leave_min", 3, source="parent")
    now[0] = 120
    assert p.tick() is None
    assert p.note_face() is None
    settings.set("presence_leave_min", 1, source="parent")
    now[0] = 180
    assert p.note_face().kind == "welcome"
    now[0] = 240
    assert p.tick().kind == "leave_tip"
    for invalid in (True, 0, 31, "3"):
        with pytest.raises(ValueError):
            settings.set("presence_leave_min", invalid, source="parent")


@pytest.mark.parametrize("notifications", [None, False, True])
def test_away_notification_episode_and_return(notifications, tmp_path, monkeypatch):
    from threading import Event
    from stackchan_mcp.parent_recovery import RecoveryStore
    from stackchan_mcp.stuck_notify import PARENT_HELPER_BOT

    monkeypatch.setenv("STACKCHAN_STATE_DIR", str(tmp_path))
    finished = Event()
    original = RecoveryStore.presence_away_event

    def record(store, sent):
        original(store, sent)
        finished.set()

    monkeypatch.setattr(RecoveryStore, "presence_away_event", record)
    settings = {"camera_presence": True, "presence_leave_min": 3}
    if notifications is not None:
        settings["notifications"] = notifications
    calls = []
    now = [0]
    p = CameraPresence(clock=lambda: now[0], settings_getter=lambda: settings,
                       send=lambda *args: calls.append(args))
    now[0] = 180
    assert p.tick().kind == "leave_tip"
    if notifications is True:
        assert finished.wait(2)
        assert calls == [(PARENT_HELPER_BOT, "【鈴鈴提示】娜娜行開咗 3 分鐘。")]
        assert RecoveryStore().events()[0]["sent"] is True
    assert p.tick() is None
    assert p.note_face().kind == "welcome"
    finished.clear()
    now[0] = 360
    assert p.tick().kind == "leave_tip"
    if notifications is True:
        assert finished.wait(2)
        assert len(calls) == 2
        assert [e["kind"] for e in RecoveryStore().events()] == ["presence_away"] * 2
    else:
        assert calls == []


def test_notification_off_suppressed_and_nonblocking_failure(tmp_path, monkeypatch, caplog):
    from threading import Event, current_thread
    from stackchan_mcp.parent_recovery import RecoveryStore

    monkeypatch.setenv("STACKCHAN_STATE_DIR", str(tmp_path))
    entered, release, finished = Event(), Event(), Event()
    original = RecoveryStore.presence_away_event

    def record(store, sent):
        original(store, sent)
        finished.set()

    monkeypatch.setattr(RecoveryStore, "presence_away_event", record)

    def send(*args):
        assert current_thread().daemon
        entered.set()
        assert release.wait(2)
        raise RuntimeError("SECRET payload")

    now = [0]
    p = CameraPresence(clock=lambda: now[0],
                       settings_getter=lambda: {"notifications": True}, send=send)
    now[0] = 500
    assert p.tick() is None
    p.on_parent_setting(True)
    assert p.tick() is None  # Enable grace.
    p.set_suppress("camera_detector", True)
    now[0] = 1000
    assert p.tick() is None
    assert not entered.is_set()
    p.set_suppress("camera_detector", False)
    now[0] = 1120
    try:
        assert p.tick().kind == "leave_tip"
        assert entered.wait(2)  # Sender remains blocked while tick has returned.
        assert p.tick() is None
    finally:
        release.set()
    assert finished.wait(2)
    assert RecoveryStore().events() == [{"id": "1", "kind": "presence_away", "sent": False}]
    assert "presence-away parent send failed" in caplog.text
    assert "SECRET" not in caplog.text


def test_leave_tip_rotates_pool():
    from stackchan_mcp.camera_presence import LEAVE_CLIPS

    now = [0.0]
    p = CameraPresence(clock=lambda: now[0])
    p.start_homework_session()
    seen = []
    for _ in range(3):
        now[0] += 1000
        p.note_face()
        now[0] += 200
        seen.append(p.tick().clip)
    assert seen == list(LEAVE_CLIPS)
