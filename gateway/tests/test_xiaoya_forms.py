# SPDX-License-Identifier: MIT
"""鈴鈴 growth forms: progress -> form rules, daily reset, device pushes."""

import asyncio
import json
import stat

from aiohttp.test_utils import TestClient, TestServer
import pytest

from stackchan_mcp.parent_api import create_parent_app
from stackchan_mcp.parent_cli import main
from stackchan_mcp.parent_settings import ParentSettings
from stackchan_mcp.xiaoya_forms import XiaoyaForms, form_for_progress, validate_progress


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


@pytest.mark.parametrize("done,total", [(-1, 3), (4, 3), (1, 51), ("1", 3), (True, 3), (1.0, 3)])
def test_validation(done, total):
    with pytest.raises(ValueError):
        validate_progress(done, total)


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
    return XiaoyaForms(tmp_path / "private" / "forms.json", today=day)


@pytest.mark.asyncio
async def test_only_up_and_push_animates_skipped_levels(forms, tmp_path):
    sent = []

    async def sender(message):
        sent.append(message)

    await forms.register_sender(sender)
    assert sent == [{"type": "xiaoya", "cmd": "form", "form": 1, "from": 1, "animate": False}]
    forms.unregister_sender(sender)
    # Offline: two jumps; device must animate 1 -> 3 on reconnect in one go.
    forms.update_progress(1, 3, source="test")
    forms.update_progress(2, 3, source="test")
    await forms.register_sender(sender)
    assert sent[-1] == {"type": "xiaoya", "cmd": "form", "form": 3, "from": 1, "animate": True}
    # Fewer done later (list edited) never lowers the form.
    snap = forms.update_progress(1, 5, source="test")
    assert snap["form"] == 3
    await asyncio.sleep(0)
    assert len(sent) == 2  # nothing new to announce
    forms.update_progress(5, 5, source="test")
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

    forms.update_progress(3, 3, source="test")
    await forms.register_sender(sender)
    assert sent[-1]["form"] == 4 and sent[-1]["animate"] is True
    forms.unregister_sender(sender)
    day.value = "2026-10-25"
    assert forms.snapshot()["form"] == 1
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

    forms.update_progress(2, 3, source="test")
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


@pytest.mark.asyncio
async def test_parent_api_and_cli(tmp_path, monkeypatch, forms, capsys):
    monkeypatch.setenv("STACKCHAN_TOKEN", "child-secret")
    monkeypatch.setenv("STACKCHAN_PARENT_TOKEN", "parent-secret")
    settings = ParentSettings(tmp_path / "settings.json")
    app = create_parent_app("parent-secret", settings, forms=forms)
    async with TestClient(TestServer(app)) as client:
        url = "/parent/v1/homework-progress"
        auth = {"Authorization": "Bearer parent-secret"}
        assert (await client.post(url, json={"done": 1, "total": 3})).status == 401
        bad = await client.post(url, headers=auth, json={"done": 4, "total": 3})
        assert bad.status == 400
        ok = await client.post(url, headers=auth, json={"done": 2, "total": 3})
        assert ok.status == 200
        body = await ok.json()
        assert body["forms"]["form"] == 3 and body["forms"]["form_name_zh"] == "寶石貓"
        base = str(client.make_url("")).rstrip("/")
        assert await asyncio.to_thread(main, ["--url", base, "homework", "3", "3"]) == 0
        assert '"form": 4' in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "homework"]) == 0
        assert "星冠貓" in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "homework", "3"]) == 1


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
    assert sent and sent[-1]["cmd"] == "form" and sent[-1]["form"] == 1
    forms.unregister_sender(sender)
