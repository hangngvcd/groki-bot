# SPDX-License-Identifier: MIT
import json
import stat

import pytest

from stackchan_mcp.xiaoya_games import XiaoyaGames, minigame_message
from stackchan_mcp.xiaoya_forms import XiaoyaForms


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


@pytest.fixture
def games(tmp_path, monkeypatch):
    import stackchan_mcp.xiaoya_games as module

    clock = Clock()
    game = XiaoyaGames(
        tmp_path / "private/games.json", clock=clock, pin_source=lambda: ""
    )
    monkeypatch.setattr(module, "_games", game)
    return game


def start(games, game="G1"):
    games.pin_source = lambda: game
    games.on_level_up(1, 2)
    return games.start_pending()


def test_forms_only_evolution_and_redo(games, tmp_path):
    forms = XiaoyaForms(tmp_path / "forms.json")
    forms.add_items(["a", "b", "c", "d", "e", "f"], source="test")
    assert not forms.mark_done("a", source="child")["game_unlock"]
    result = forms.mark_done("b", source="child")
    assert result["level_up"] and result["game_unlock"]["game"] == "G1"
    games.start_pending()
    games.on_double_press()
    forms.redo_items(["b"], source="parent")
    assert not forms.mark_done("b", source="child")["game_unlock"]
    assert games.snapshot()["played_today"] == ["G1"]
    assert not games.has_pending()


def test_no_bank_decline_rotation(games):
    for expected in ["G1", "G2", "G3", "G1"]:
        first = games.on_level_up(1, 3)
        assert first["game"] == expected
        assert games.on_level_up(3, 4) is None
        assert games.peek_pending() == first
        assert games.start_pending(accept=False)["wasted"]
        assert not games.start_pending()["ok"]


def test_double_press_pending_and_active(games):
    games.on_level_up(1, 2)
    assert games.on_double_press()["messages"][0]["reason"] == "decline"
    start(games)
    assert games.on_level_up(2, 3) is None
    assert (
        games.note_device_event({"event": "boot_press", "kind": "double"})["messages"][
            0
        ]["reason"]
        == "quit"
    )
    assert not games.active and not games.has_pending()


@pytest.mark.parametrize(
    "delay,leaves", [(0.2, 3), (0.4, 2), (0.79, 2), (0.8, 1), (1.5, 1), (2.1, 0)]
)
def test_g1_thresholds(games, delay, leaves):
    start(games)
    games.clock.value = games.active["due"]
    assert games.tick()["messages"] == [minigame_message("cue")]
    games.clock.value += delay
    assert games.on_short_press()["leaves"] == leaves


def test_g1_early_miss_and_five_rounds(games):
    start(games)
    assert games.on_short_press()["leaves"] == 0
    for _ in range(4):
        games.clock.value = games.active["due"]
        games.tick()
        games.clock.value = games.active["due"]
        result = games.tick()
        assert result["leaves"] == 0
    assert result["messages"][-1]["reason"] == "done"
    assert games.active is None


@pytest.mark.parametrize("difference,leaves", [(0, 3), (-1, 2), (1, 2), (2, 0)])
def test_g2_counts(games, difference, leaves):
    sent = start(games, "G2")["messages"]
    target = games.active["target"]
    while games.active["phase"] == "beeps":
        games.clock.value = games.active["due"]
        sent.extend(games.tick()["messages"])
    assert sum(m["action"] == "cue" for m in sent) == target
    for _ in range(target + difference):
        games.on_short_press()
    games.clock.value = games.active["due"]
    result = games.tick()
    assert result["leaves"] == leaves
    assert bool(result["spoken"]) == (abs(difference) > 1)
    assert games.active is None


def test_g3_end_no_score(games):
    sent = start(games, "G3")["messages"]
    while games.active:
        games.clock.value = games.active["due"]
        result = games.tick()
        sent.extend(result["messages"])
    assert result["spoken"] == "休息得好好"
    assert [m["phase"] for m in sent if m["action"] == "breathe"] == ["in", "out"] * 8
    assert not any(m["action"] == "leaves" for m in sent)


def test_expiry_restart_and_private_persistence(games):
    games.on_level_up(1, 2)
    assert stat.S_IMODE(games.path.stat().st_mode) == 0o600
    games.clock.value = 16
    assert not games.has_pending()
    games.on_level_up(2, 3)
    again = XiaoyaGames(games.path, clock=games.clock)
    assert not again.has_pending()
    assert json.loads(games.path.read_text())["pending"] is None
    assert again.snapshot()["next_index"] == 2


def test_refusal_pin_and_contract(games):
    assert not games.refuse_play()["ok"]
    assert "變身" in games.refuse_play()["spoken"]
    assert (
        "中文"
        in games.refuse_play({"form": 1, "items": [{"title": "中文", "done": False}]})[
            "spoken"
        ]
    )
    assert start(games, "G3")["messages"][0] == {
        "type": "xiaoya",
        "cmd": "minigame",
        "action": "enter",
        "game": "G3",
        "form": 2,
    }
    assert minigame_message("leaves", count=3)["count"] == 3
    for action, fields in [
        ("leaves", {"count": 4}),
        ("enter", {"game": "G4", "form": 2}),
        ("breathe", {"phase": "bad"}),
        ("exit", {"reason": "bad"}),
    ]:
        with pytest.raises(ValueError):
            minigame_message(action, **fields)


@pytest.mark.asyncio
async def test_voice_dispatch(games, tmp_path, monkeypatch):
    import stackchan_mcp.xiaoya_forms as module
    from stackchan_mcp.voice_tools import VoiceToolExecutor, build_function_declarations

    forms = XiaoyaForms(tmp_path / "forms.json")
    monkeypatch.setattr(module, "_forms", forms)
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    assert "play_minigame" in [d["name"] for d in build_function_declarations()]
    executor = VoiceToolExecutor(instruction_provider=lambda **kwargs: "")
    result = await executor._dispatch_tool_impl("play_minigame", {})
    assert not result["ok"] and result["spoken"]
    forms.add_items(["中文"], source="parent")
    result = await executor._dispatch_tool_impl("homework_done", {"item": "中文"})
    from stackchan_mcp.homework_photo import get_homework_photo
    photo = get_homework_photo()
    photo.send = lambda *args: None
    result = photo.complete(False)
    assert result["game_unlock"] and result["invite_spoken"]
    assert games.begin_invite_after_evolve()["ok"]
    result = await executor._dispatch_tool_impl("play_minigame", {})
    assert (
        result["ok"] and result["game"] == "G1" and "end voice turn" in result["note"]
    )


def test_parent_pin_validation():
    from stackchan_mcp.parent_settings import validate, device_support

    from stackchan_mcp.parent_cli import cli_value

    assert cli_value("pin_minigame", "G2") == "G2"
    assert validate({"pin_minigame": "G2"})["pin_minigame"] == "G2"
    with pytest.raises(ValueError):
        validate({"pin_minigame": "G4"})
    assert device_support()["pin_minigame"]["applied_to_device"] is False


def test_device_short_event_and_daily_reset(games):
    start(games)
    result = games.note_device_event(
        {"type": "xiaoya", "event": "minigame_press", "kind": "short"}
    )
    assert result["leaves"] == 0
    games.today = lambda: "2099-01-01"
    assert games.active is None
    assert games.snapshot()["played_today"] == []


def test_corrupt_state_is_safe(tmp_path):
    path = tmp_path / "games.json"
    path.write_text("{bad json")
    games = XiaoyaGames(path)
    assert not games.has_pending() and games.active is None
