# SPDX-License-Identifier: MIT
"""鈴鈴 growth forms: homework list -> form rules, daily reset, device pushes."""

import asyncio
import json
import logging
import stat

from aiohttp.test_utils import TestClient, TestServer
import pytest

from stackchan_mcp.parent_api import create_parent_app
from stackchan_mcp.parent_cli import main
from stackchan_mcp.parent_settings import ParentSettings
from stackchan_mcp.xiaoya_forms import XiaoyaForms, clean_title, form_for_progress


@pytest.mark.parametrize(
    "done,total,form",
    [
        (0, 0, 1),
        (0, 3, 1),
        (1, 3, 2),
        (2, 3, 3),
        (3, 3, 4),
        (1, 4, 1),
        (2, 4, 2),
        (3, 4, 3),
        (4, 4, 4),
        (1, 1, 4),
        (0, 1, 1),
        (1, 2, 2),
        (2, 6, 2),
        (4, 6, 3),
        (5, 6, 3),
    ],
)
def test_form_rules(done, total, form):
    assert form_for_progress(done, total) == form


@pytest.mark.parametrize("title", ["", "   ", 3, None, "x" * 81])
def test_title_validation(title):
    with pytest.raises(ValueError):
        clean_title(title)


def set_progress(forms, done, total):
    """Test helper: a fresh list of `total` items with the first `done` marked by 娜娜."""
    if forms.snapshot()["items"]:
        forms.remove_items([item["id"] for item in forms.snapshot()["items"]], source="test")
    snap = forms.add_items([f"功課{i}" for i in range(total)], source="test") if total else forms.snapshot()
    for item in snap["items"][:done]:
        snap = forms.mark_done(item["id"], source="test")
    return snap


class Day:
    def __init__(self, value="2026-10-24"):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def day():
    return Day()


@pytest.fixture
def forms(tmp_path, day):
    return XiaoyaForms(tmp_path / "private" / "forms.json", today=day, volume_source=lambda: 40)


@pytest.mark.asyncio
async def test_only_up_and_push_animates_skipped_levels(forms, tmp_path):
    sent = []

    async def sender(message):
        sent.append(message)

    await forms.register_sender(sender)
    assert sent == [{"type": "xiaoya", "cmd": "form", "form": 1, "from": 1, "animate": False}]
    forms.unregister_sender(sender)
    # Offline: two jumps; device must animate 1 -> 3 on reconnect in one go.
    snap = forms.add_items(["中文", "數學", "英文"], source="test")
    forms.mark_done("中文", source="test")
    forms.mark_done("#2", source="test")
    await forms.register_sender(sender)
    assert sent[-1] == {"type": "xiaoya", "cmd": "form", "form": 3, "from": 1, "animate": True}
    # Dad adds homework later: 2/5 would be F2, but the form never goes down.
    snap = forms.add_items(["常識", "默書"], source="test")
    assert (snap["done"], snap["total"], snap["form"], snap["level_up"]) == (2, 5, 3, False)
    await asyncio.sleep(0)
    assert len(sent) == 2  # nothing new to announce
    for item in snap["items"]:
        forms.mark_done(item["id"], source="test")
    for _ in range(3):
        await asyncio.sleep(0)
    assert sent[-1] == {"type": "xiaoya", "cmd": "form", "form": 4, "from": 3, "animate": True}
    path = tmp_path / "private" / "forms.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["announced_form"] == 4
    forms.unregister_sender(sender)


@pytest.mark.asyncio
async def test_daily_reset_sends_silent_f1(forms, day, tmp_path):
    sent = []

    async def sender(message):
        sent.append(message)

    set_progress(forms, 3, 3)
    await forms.register_sender(sender)
    assert sent[-1]["form"] == 4 and sent[-1]["animate"] is True
    forms.unregister_sender(sender)
    day.value = "2026-10-25"
    assert forms.snapshot()["form"] == 1 and forms.snapshot()["items"] == []
    await forms.register_sender(sender)
    assert sent[-1] == {"type": "xiaoya", "cmd": "form", "form": 1, "from": 4, "animate": False}
    # Persisted across restarts.
    again = XiaoyaForms(tmp_path / "private" / "forms.json", today=day)
    assert again.snapshot()["form"] == 1 and again.snapshot()["announced_form"] == 1
    forms.unregister_sender(sender)


@pytest.mark.asyncio
async def test_failed_push_retries_on_reconnect(forms):
    calls = []

    async def broken(message):
        calls.append(message)
        raise ConnectionError("gone")

    set_progress(forms, 2, 3)
    assert await forms.register_sender(broken) is False
    forms.unregister_sender(broken)
    sent = []

    async def sender(message):
        sent.append(message)

    await forms.register_sender(sender)
    assert sent[-1]["from"] == 1 and sent[-1]["form"] == 3 and sent[-1]["animate"]
    forms.unregister_sender(sender)


def test_corrupt_state_starts_at_f1(tmp_path, day):
    path = tmp_path / "forms.json"
    path.write_text("{not json")
    assert XiaoyaForms(path, today=day).snapshot()["form"] == 1
    path.write_text(json.dumps({"date": "2026-10-24", "done": 9, "total": 3, "form": 4}))
    assert XiaoyaForms(path, today=day).snapshot()["form"] == 1
    # A state file from before the homework list keeps today's form, empty list.
    path.write_text(json.dumps({"date": "2026-10-24", "done": 2, "total": 3, "form": 3, "announced_form": 3}))
    snap = XiaoyaForms(path, today=day).snapshot()
    assert (snap["form"], snap["items"], snap["announced_form"]) == (3, [], 3)
    path.write_text(json.dumps({"date": "2026-10-24", "items": [{"id": 1, "title": "x", "done": "yes"}], "form": 1}))
    assert XiaoyaForms(path, today=day).snapshot()["items"] == []


@pytest.mark.parametrize("done,total,form", [(0, 0, 1), (1, 3, 2), (2, 3, 3), (3, 3, 4), (1, 4, 1), (2, 4, 2)])
def test_list_drives_form(forms, done, total, form):
    assert set_progress(forms, done, total)["form"] == form


def test_list_edits(forms):
    snap = forms.add_items(["中文作文", "數學 p.12", "英文默書"], source="test")
    assert [item["id"] for item in snap["items"]] == [1, 2, 3] and snap["form"] == 1
    done = forms.mark_done("數學", source="test")
    assert done["item"]["title"] == "數學 p.12" and done["level_up"] and done["form"] == 2
    again = forms.mark_done(2, source="test")
    assert again["already_done"] and not again["level_up"]
    # Redo request: pending again, form stays F2.
    redo = forms.redo_items(["#2"], source="test")
    assert redo["done"] == 0 and redo["form"] == 2
    forms.mark_done("數學", source="test")
    # Removing an undone item recomputes: 1/2 -> F2 (stays), then 1/1 -> F4.
    assert forms.remove_items(["中文作文"], source="test")["form"] == 2
    removed = forms.remove_items(["英文默書"], source="test")
    assert removed["form"] == 4 and removed["level_up"]
    with pytest.raises(LookupError):
        forms.mark_done("常識", source="test")
    forms.add_items(["英文 A", "英文 B"], source="test")
    with pytest.raises(LookupError, match="ambiguous"):
        forms.mark_done("英文", source="test")
    with pytest.raises(LookupError):
        forms.remove_items(["#99"], source="test")
    with pytest.raises(ValueError):
        forms.add_items([], source="test")


@pytest.mark.asyncio
async def test_parent_api_and_cli(tmp_path, monkeypatch, forms, capsys):
    monkeypatch.setenv("STACKCHAN_TOKEN", "child-secret")
    monkeypatch.setenv("STACKCHAN_PARENT_TOKEN", "parent-secret")
    settings = ParentSettings(tmp_path / "settings.json")
    app = create_parent_app("parent-secret", settings, forms=forms)
    async with TestClient(TestServer(app)) as client:
        url = "/parent/v1/homework"
        auth = {"Authorization": "Bearer parent-secret"}
        assert (await client.post(url, json={"add": ["中文"]})).status == 401
        # The old progress push is gone: the parent assistant can't report progress.
        gone = await client.post("/parent/v1/homework-progress", headers=auth, json={"done": 1, "total": 3})
        assert gone.status in {404, 405}
        for bad in ({"done": 1, "total": 3}, {"add": []}, {"add": ["a"], "remove": ["a"]}, ["a"]):
            assert (await client.post(url, headers=auth, json=bad)).status == 400
        ok = await client.post(url, headers=auth, json={"add": ["中文", "數學", "英文"]})
        assert ok.status == 200
        body = await ok.json()
        assert body["forms"]["total"] == 3 and body["forms"]["form"] == 1
        assert (await client.post(url, headers=auth, json={"remove": ["常識"]})).status == 404
        forms.mark_done("中文", source="test")
        forms.mark_done("數學", source="test")
        body = await (await client.get(url, headers=auth)).json()
        assert body["forms"]["form"] == 3 and body["forms"]["form_name_zh"] == "寶石貓"
        base = str(client.make_url("")).rstrip("/")
        assert await asyncio.to_thread(main, ["--url", base, "homework", "add", "默書"]) == 0
        out = capsys.readouterr().out
        assert "#4 · 默書" in out and "3/4" not in out and "2/4" in out and "F3" in out
        assert await asyncio.to_thread(main, ["--url", base, "homework", "remove", "英文", "#4"]) == 0
        assert "F4 星冠貓" in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "homework", "redo", "中文"]) == 0
        assert "1/2" in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "homework"]) == 0
        assert "星冠貓" in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "homework", "add"]) == 1
        with pytest.raises(SystemExit):
            main(["--url", base, "homework", "3", "3"])  # old progress form is gone


@pytest.mark.asyncio
async def test_child_voice_tools_mark_done(monkeypatch, forms):
    import stackchan_mcp.xiaoya_forms as module
    from stackchan_mcp.voice_tools import VoiceToolExecutor, build_function_declarations

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setattr(module, "_forms", forms)
    names = [d.name if hasattr(d, "name") else d["name"] for d in build_function_declarations()]
    assert {"homework_list", "homework_done"} <= set(names)
    executor = VoiceToolExecutor(instruction_provider=lambda **kwargs: "")
    empty = await executor._dispatch_tool_impl("homework_list", {})
    assert empty["items"] == [] and empty["spoken"]
    forms.add_items(["中文", "數學"], source="test")
    listed = await executor._dispatch_tool_impl("homework_list", {})
    assert [item["title"] for item in listed["items"]] == ["中文", "數學"]
    done = await executor._dispatch_tool_impl("homework_done", {"item": "中文"})
    assert done["ok"] and done["level_up"] and (done["done"], done["total"]) == (1, 2)
    missing = await executor._dispatch_tool_impl("homework_done", {"item": "常識"})
    assert not missing["ok"] and missing["spoken"]
    assert forms.snapshot()["form"] == 2


@pytest.mark.asyncio
async def test_intro_replay_api_and_cli(tmp_path, monkeypatch, forms, capsys):
    monkeypatch.setenv("STACKCHAN_PARENT_TOKEN", "parent-secret")
    settings = ParentSettings(tmp_path / "settings.json")
    app = create_parent_app("parent-secret", settings, forms=forms)
    sent = []

    async def sender(message):
        sent.append(message)

    async with TestClient(TestServer(app)) as client:
        auth = {"Authorization": "Bearer parent-secret"}
        offline = await client.post("/parent/v1/intro", headers=auth, json={})
        assert offline.status == 409
        await forms.register_sender(sender)
        sent.clear()
        assert (await client.post("/parent/v1/intro", headers=auth, json={})).status == 200
        assert sent == [{"type": "xiaoya", "cmd": "intro", "force": True}]
        base = str(client.make_url("")).rstrip("/")
        assert await asyncio.to_thread(main, ["--url", base, "intro"]) == 0
        assert len(sent) == 2
        forms.unregister_sender(sender)


@pytest.mark.asyncio
async def test_form_pushed_when_device_reports_session_ready(forms):
    sent = []

    async def sender(message):
        sent.append(message)

    assert forms.register_sender(sender, push=False) is None
    await asyncio.sleep(0)
    assert sent == []
    forms.note_device_event({"type": "xiaoya", "event": "form_state", "form": 1})
    for _ in range(5):
        await asyncio.sleep(0)
    cmds = [m["cmd"] for m in sent]
    assert "form" in cmds and next(m for m in sent if m["cmd"] == "form")["form"] == 1
    assert {"type": "xiaoya", "cmd": "volume", "volume": 40} in sent
    forms.unregister_sender(sender)


@pytest.mark.asyncio
async def test_intro_tts_fallback_uses_edge_tts_and_mutes_wake():
    from stackchan_mcp.gemini_voice_proxy import GeminiVoiceProxy
    from stackchan_mcp.xiaoya_forms import INTRO_TEXT, INTRO_VOICE

    spoken, gate_calls = [], []

    class Gate:
        def set_local_speech(self, value):
            gate_calls.append(value)

    class Provider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)

        async def speak_text(self, text):
            spoken.append((self.kwargs.get("voice"), text, list(gate_calls)))
            return {"ok": True}

    proxy = GeminiVoiceProxy(esp32_ref=None, edge_tts_provider_factory=Provider)
    # No edge-tts yet (not connected): skipped quietly, nothing spoken.
    proxy.handle_xiaoya_event({"type": "xiaoya", "event": "intro_tts"})
    assert not proxy._tts_tasks
    proxy._edge_tts = Provider(codec=None, send_to_device=None, send_lock=asyncio.Lock(),
                               tts_start_delay_s=0, frame_interval_s=0, on_device_state=None)
    proxy._wake_gate = Gate()
    proxy.handle_xiaoya_event({"type": "xiaoya", "event": "intro_tts"})
    await asyncio.gather(*list(proxy._tts_tasks))
    assert spoken == [(INTRO_VOICE, INTRO_TEXT, [True])]
    assert gate_calls == [True, False]


def test_intro_text_matches_clip_script():
    from pathlib import Path
    from stackchan_mcp.xiaoya_forms import INTRO_TEXT, INTRO_VOICE

    lines = json.loads((Path(__file__).parents[2] / "tools/lingling_assets/lines.json").read_text())
    assert lines["clips"]["vo.intro.1"] == INTRO_TEXT and lines["voice"] == INTRO_VOICE


async def _drain():
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_boot_report_logged_and_volume_resent(forms, caplog):
    sent = []

    async def sender(message):
        sent.append(message)

    forms.register_sender(sender, push=False)
    # Before session ready nothing is sent (the device may still be booting).
    assert await forms.send_volume(70) is False and sent == []
    with caplog.at_level(logging.INFO):
        forms.note_device_event(
            {"type": "xiaoya", "event": "form_state", "form": 1, "volume": 100,
             "reset_reason": "brownout", "uptime_ms": 11500}
        )
        await _drain()
    assert any(r.levelno == logging.WARNING and "reset_reason=brownout" in r.getMessage()
               for r in caplog.records)
    assert {"type": "xiaoya", "cmd": "volume", "volume": 40} in sent
    device = forms.snapshot()["device"]
    assert device["reset_reason"] == "brownout" and device["volume"] == 100
    assert forms.session_ready
    sent.clear()
    assert await forms.send_volume(15) is True
    assert sent == [{"type": "xiaoya", "cmd": "volume", "volume": 15}]
    forms.unregister_sender(sender)
    assert not forms.session_ready and await forms.send_volume(15) is False
    with caplog.at_level(logging.INFO):
        forms.note_device_event({"type": "xiaoya", "event": "form_state", "form": 1,
                                 "reset_reason": "poweron"})
    assert not any(r.levelno == logging.WARNING and "poweron" in r.getMessage() for r in caplog.records)


def test_panic_breadcrumb_logged_as_warning(forms, caplog):
    crumb = "abort core0 pc=40375a2e task=conversation bt=4037fd1a,420b1c2d - abort() was called"
    with caplog.at_level(logging.INFO):
        forms.note_device_event(
            {"type": "xiaoya", "event": "form_state", "form": 1, "volume": 40,
             "reset_reason": "panic", "panic": crumb, "uptime_ms": 9000}
        )
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("panic breadcrumb" in m and crumb in m for m in warnings)
    assert forms.snapshot()["device"]["panic"] == crumb
    caplog.clear()
    with caplog.at_level(logging.INFO):
        forms.note_device_event({"type": "xiaoya", "event": "form_state", "form": 1,
                                 "reset_reason": "poweron", "panic": ""})
    assert not any("panic breadcrumb" in r.getMessage() for r in caplog.records)
    assert "panic" not in forms.snapshot()["device"]


@pytest.mark.parametrize("bad", [-1, 101, 40.5, "40", True, None])
def test_volume_message_validation(bad):
    from stackchan_mcp.xiaoya_forms import volume_message

    with pytest.raises(ValueError):
        volume_message(bad)
    assert volume_message(0) == {"type": "xiaoya", "cmd": "volume", "volume": 0}


@pytest.mark.asyncio
async def test_volume_api_cli(tmp_path, monkeypatch, forms, capsys):
    monkeypatch.setenv("STACKCHAN_PARENT_TOKEN", "parent-secret")
    settings = ParentSettings(tmp_path / "settings.json")
    app = create_parent_app("parent-secret", settings, forms=forms)
    sent = []

    async def sender(message):
        sent.append(message)

    async with TestClient(TestServer(app)) as client:
        url = "/parent/v1/volume"
        auth = {"Authorization": "Bearer parent-secret"}
        assert (await client.get(url)).status == 401
        body = await (await client.get(url, headers=auth)).json()
        assert body["volume"] == 40 and body["device_connected"] is False
        for bad in ({"volume": 101}, {"volume": "50"}, {"volume": 5, "x": 1}, {}, [5]):
            assert (await client.post(url, headers=auth, json=bad)).status == 400
        offline = await (await client.post(url, headers=auth, json={"volume": 25})).json()
        assert offline["volume"] == 25 and offline["applies"] == "next_connect"
        assert settings.get()["volume"] == 25 and sent == []
        forms.register_sender(sender, push=False)
        forms.note_device_event({"type": "xiaoya", "event": "form_state", "form": 1,
                                 "volume": 40, "reset_reason": "poweron"})
        await _drain()
        live = await (await client.post(url, headers=auth, json={"volume": 30})).json()
        await _drain()
        assert live["applies"] == "now" and live["last_reset_reason"] == "poweron"
        assert sent[-1] == {"type": "xiaoya", "cmd": "volume", "volume": 30}
        base = str(client.make_url("")).rstrip("/")
        assert await asyncio.to_thread(main, ["--url", base, "volume", "20"]) == 0
        out = capsys.readouterr().out
        assert "音量 20" in out and "已即時套用" in out
        await _drain()
        assert sent[-1]["volume"] == 20
        assert await asyncio.to_thread(main, ["--url", base, "volume"]) == 0
        assert "poweron" in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "volume", "150"]) == 1
        forms.unregister_sender(sender)
