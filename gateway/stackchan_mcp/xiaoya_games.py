# SPDX-License-Identifier: MIT
"""Local evolve games, tickets 27/30; firmware contract is in docs.

One immediate slot per evolve commit, including skipped forms (one animation).
A second evolution while pending/active grants nothing: there is no queue.
Rotation advances at grant, including declines. Invites expire after 15 seconds;
restart abandons pending/active slots, so neither becomes a later-play bank.
The local-loop owner calls consume_invite_start after animation/voice shutdown,
then tick regularly and sends each returned message using forms Sender.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
import random
import tempfile
import time
from typing import Any, Callable

from .parent_settings import get_parent_settings, private_directory

GAMES = ("G1", "G2", "G3")
INVITE_SECONDS = 15.0
G1_ROUNDS = 5
G1_MISS_SECONDS = 2.0
G2_BEEP_SECONDS = 0.6
G2_INPUT_SECONDS = 6.0
BREATH_SECONDS = 4.0
BREATH_PHASES = 16  # 64 seconds


def minigame_message(action: str, **fields: Any) -> dict[str, Any]:
    allowed = {"enter", "cue", "leaves", "breathe", "exit"}
    if action not in allowed:
        raise ValueError("unknown minigame action")
    if action == "enter" and (
        fields.get("game") not in GAMES
        or type(fields.get("form")) is not int
        or not 1 <= fields["form"] <= 4
    ):
        raise ValueError("bad game/form")
    if action == "leaves" and (
        type(fields.get("count")) is not int or not 0 <= fields["count"] <= 3
    ):
        raise ValueError("bad leaves")
    if action == "breathe" and fields.get("phase") not in ("in", "out"):
        raise ValueError("bad phase")
    if action == "exit" and fields.get("reason") not in ("done", "quit", "decline"):
        raise ValueError("bad exit reason")
    return {"type": "xiaoya", "cmd": "minigame", "action": action, **fields}


def invite_spoken(game: str) -> str:
    return "變身喇！嚟玩！"


class XiaoyaGames:
    def __init__(
        self,
        path=None,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng=None,
        today=None,
        pin_source=None,
    ):
        self.path = Path(
            path
            or os.getenv(
                "STACKCHAN_XIAOYA_GAMES_PATH", str(Path(os.getenv("STACKCHAN_STATE_DIR", "~/.stackchan")) / "xiaoya_games.json")
            )
        ).expanduser()
        self.clock = clock
        self.rng = rng or random.Random()
        self.today = today or (
            lambda: dt.datetime.now().astimezone().date().isoformat()
        )
        self.pin_source = pin_source or (
            lambda: get_parent_settings().get().get("pin_minigame", "")
        )
        self._state = self._fresh()
        try:
            data = json.loads(self.path.read_text())
            if (
                not isinstance(data, dict)
                or type(data["next_index"]) is not int
                or not 0 <= data["next_index"] < 3
                or not isinstance(data["played_today"], list)
                or any(g not in GAMES for g in data["played_today"])
                or data.get("pin_game") not in (None, *GAMES)
            ):
                raise ValueError("bad games state")
            if data["date"] == self.today():
                self._state.update(data)
                self._state.update(pending=None, active=None)
                self._save()
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _fresh(self):
        return dict(
            date=self.today(),
            next_index=0,
            pending=None,
            active=None,
            played_today=[],
            pin_game=None,
        )

    def _save(self):
        private_directory(self.path.parent)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".xiaoya-games-")
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(self._state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _refresh(self):
        if self._state["date"] != self.today():
            self._state = self._fresh()
            self._save()
        pending = self._state["pending"]
        if pending and self.clock() >= pending["expires_at"]:
            self._state["pending"] = None
            self._save()

    def snapshot(self):
        self._refresh()
        return copy.deepcopy(self._state)

    @property
    def active(self):
        return self.snapshot()["active"]

    def has_pending(self) -> bool:
        return self.peek_pending() is not None

    def peek_pending(self) -> dict | None:
        return self.snapshot()["pending"]

    def on_level_up(self, previous_form, new_form):
        self._refresh()
        if (
            not 1 <= previous_form < new_form <= 4
            or self._state["pending"]
            or self._state["active"]
        ):
            return None
        pin = self.pin_source() or self._state["pin_game"]
        game = pin if pin in GAMES else GAMES[self._state["next_index"]]
        self._state["next_index"] = (self._state["next_index"] + 1) % 3
        self._state["pending"] = dict(
            game=game,
            from_form=previous_form,
            to_form=new_form,
            granted_at=dt.datetime.now().astimezone().isoformat(),
            expires_at=self.clock() + INVITE_SECONDS,
        )
        self._save()
        return self.peek_pending()

    def refuse_play(self, forms_snapshot=None):
        spoken = "再做吓功課，我變身嗰陣一齊玩。"
        if forms_snapshot and forms_snapshot.get("form", 4) < 4:
            items = forms_snapshot.get("items", [])
            remaining = [item["title"] for item in items if not item["done"]]
            if remaining:
                spoken = (
                    "仲有" + "、".join(remaining) + "，再做吓功課，我變身嗰陣一齊玩。"
                )
        return dict(ok=False, spoken=spoken)

    def begin_invite_after_evolve(self):
        pending = self.peek_pending()
        return dict(
            ok=bool(pending),
            game_unlock=pending,
            invite_spoken=invite_spoken(pending["game"]) if pending else "",
            messages=[],
            note="After form animation, stop voice session and call consume_invite_start immediately; never send_task or bank.",
        )

    def consume_invite_start(self):
        return self.start_pending()

    def start_pending(self, *, accept=True):
        pending = self.peek_pending()
        if not pending:
            return self.refuse_play()
        self._state["pending"] = None
        if not accept:
            self._save()
            return dict(
                ok=True,
                wasted=True,
                messages=[minigame_message("exit", reason="decline")],
            )
        now = self.clock()
        game = pending["game"]
        active = dict(game=game, form=pending["to_form"], round=0, presses=0)
        self._state["active"] = active
        self._state["played_today"].append(game)
        messages = [minigame_message("enter", game=game, form=active["form"])]
        if game == "G1":
            self._wait_g1(now)
        elif game == "G2":
            active.update(
                target=self.rng.randint(2, 6), beeps=0, phase="beeps", due=now
            )
        else:
            active.update(phase_index=0, due=now + BREATH_SECONDS)
            messages.append(minigame_message("breathe", phase="in"))
        self._save()
        return dict(
            ok=True,
            game=game,
            messages=messages,
            note="Local play; end voice turn. Never send_task.",
        )

    def _wait_g1(self, now):
        self._state["active"].update(phase="wait", due=now + self.rng.uniform(1, 4))

    def _finish(self, reason="done", spoken=""):
        self._state["active"] = None
        self._save()
        return dict(
            ok=True, spoken=spoken, messages=[minigame_message("exit", reason=reason)]
        )

    def _g1_score(self, count, now):
        active = self._state["active"]
        active["round"] += 1
        messages = [minigame_message("leaves", count=count)]
        if active["round"] >= G1_ROUNDS:
            messages += self._finish()["messages"]
        else:
            self._wait_g1(now)
            self._save()
        return dict(ok=True, leaves=count, messages=messages)

    def tick(self, now=None):
        self._refresh()
        now = self.clock() if now is None else now
        a = self._state["active"]
        if not a or now < a["due"]:
            return dict(ok=True, messages=[])
        if a["game"] == "G1":
            if a["phase"] == "cue":
                return self._g1_score(0, now)
            a.update(phase="cue", cue_at=now, due=now + G1_MISS_SECONDS)
            messages = [minigame_message("cue")]
        elif a["game"] == "G2":
            if a["phase"] == "input":
                return self.finish_g2()
            a["beeps"] += 1
            messages = [minigame_message("cue")]
            a["due"] = now + G2_BEEP_SECONDS
            if a["beeps"] == a["target"]:
                a.update(phase="input", due=now + G2_INPUT_SECONDS)
        else:
            a["phase_index"] += 1
            if a["phase_index"] >= BREATH_PHASES:
                return self._finish(spoken="休息得好好")
            a["due"] = now + BREATH_SECONDS
            messages = [
                minigame_message(
                    "breathe", phase="out" if a["phase_index"] % 2 else "in"
                )
            ]
        self._save()
        return dict(ok=True, messages=messages)

    def on_short_press(self, now=None):
        self._refresh()
        now = self.clock() if now is None else now
        a = self._state["active"]
        if not a:
            return dict(ok=False, messages=[])
        if a["game"] == "G1":
            # A press does not create a cue: only the local timer can do so.
            cue_at = a.get("cue_at", now)
            count = (
                0
                if a["phase"] != "cue" or now >= a["due"]
                else (3 if now < cue_at + 0.4 else 2 if now < cue_at + 0.8 else 1)
            )
            return self._g1_score(count, now)
        if a["game"] == "G2" and a["phase"] == "input":
            if now >= a["due"]:
                return self.finish_g2()
            a["presses"] += 1
            self._save()
        return dict(ok=True, messages=[])

    def finish_g2(self):
        a = self._state["active"]
        if not a or a["game"] != "G2" or a["phase"] != "input":
            return dict(ok=False, messages=[])
        diff = abs(a["presses"] - a["target"])
        leaves = 3 if diff == 0 else 2 if diff == 1 else 0
        result = self._finish(spoken="慢慢嚟，一齊再試。" if diff > 1 else "")
        result.update(
            leaves=leaves,
            messages=[minigame_message("leaves", count=leaves), *result["messages"]],
        )
        return result

    def on_double_press(self):
        self._refresh()
        if self._state["active"]:
            return self._finish("quit")
        return self.start_pending(accept=False)

    def note_device_event(self, message):
        event = message.get("event")
        if event == "minigame_press" or (event == "boot_press" and self.active):
            if message.get("kind") == "short":
                return self.on_short_press()
            if message.get("kind") == "double":
                return self.on_double_press()
        return dict(ok=False, messages=[])


_games: XiaoyaGames | None = None


def get_xiaoya_games() -> XiaoyaGames:
    global _games
    if _games is None:
        _games = XiaoyaGames()
    return _games


def reset_xiaoya_games() -> None:
    global _games
    _games = None
