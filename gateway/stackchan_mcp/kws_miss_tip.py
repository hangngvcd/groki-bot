"""Daily, silent parent-only guidance when voice wake has no hits."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import tempfile
import time

from .gbot_client import GrokBotClient
from .parent_settings import private_directory

logger = logging.getLogger(__name__)
PARENT_HELPER_BOT = "05d81ca9-b265-4d98-b754-d3ecd4693095"
TIP_TEXT = "【鈴鈴提示】今日語音喚醒「鈴鈴」未有命中紀錄。可試靠近部機講，或教娜娜撳 BOOT。唔使同小朋友講呢句。"


def _send(bot, text):
    GrokBotClient().send(bot, text)


class KwsMissTip:
    def __init__(self, path=None, *, today=None, send=None, clock=time.time):
        self.path = Path(
            path
            or os.getenv(
                "STACKCHAN_KWS_MISS_TIP_PATH", str(Path(os.getenv("STACKCHAN_STATE_DIR", "~/.stackchan")) / "kws_miss_tip.json")
            )
        ).expanduser()
        self.today = today or (lambda: datetime.now().astimezone().date())
        self.send = send or _send
        self.clock = clock
        self._last_check = None
        self._tasks = set()
        self._state = {}
        try:
            state = json.loads(self.path.read_text())
            if (
                isinstance(state, dict)
                and isinstance(state.get("date"), str)
                and all(
                    type(state.get(k)) is int and state[k] >= 0
                    for k in ("wake_word_count", "boot_listen_count")
                )
                and type(state.get("tipped")) is bool
            ):
                self._state = state
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logger.warning("Could not read KWS miss tip state", exc_info=True)

    def counts(self):
        day = str(self.today())
        if self._state.get("date") != day:
            self._state = dict(
                date=day, wake_word_count=0, boot_listen_count=0, tipped=False
            )
        return dict(self._state)

    def _persist(self):
        try:
            private_directory(self.path.parent)
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".kws-miss-tip-")
            try:
                with os.fdopen(fd, "w") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump(self._state, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
                name = None
                return True
            finally:
                if name is not None and os.path.exists(name):
                    os.unlink(name)
        except OSError:
            logger.warning("Could not persist KWS miss tip state", exc_info=True)
            return False

    def note_wake_word_hit(self):
        self.counts()
        self._state["wake_word_count"] += 1
        self._persist()

    def note_boot_listen(self):
        self.counts()
        self._state["boot_listen_count"] += 1
        self._persist()
        if self._state["boot_listen_count"] >= 3:
            self._maybe_tip()

    def maybe_check_connected(
        self, *, device_connected, connected_since, listening_mode, wake_available
    ):
        now = self.clock()
        if self._last_check is not None and now - self._last_check < 60:
            return
        self._last_check = now
        self.counts()
        if (
            device_connected
            and connected_since is not None
            and now - connected_since >= 2 * 60 * 60
            and listening_mode != "pass_through"
            and wake_available
        ):
            self._maybe_tip()

    def _maybe_tip(self):
        if self._state["wake_word_count"] or self._state["tipped"]:
            return
        # Reserve durably before sending: even a failed/ambiguous send must not spam.
        self._state["tipped"] = True
        if not self._persist():
            # Disk failed: allow a later retry in this process once persist works.
            self._state["tipped"] = False
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._send_safely()
        else:
            task = loop.create_task(asyncio.to_thread(self._send_safely))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def _send_safely(self):
        try:
            self.send(PARENT_HELPER_BOT, TIP_TEXT)
        except Exception:
            logger.warning("KWS miss parent tip send failed", exc_info=True)


_tip = None


def get_kws_miss_tip():
    global _tip
    if _tip is None:
        _tip = KwsMissTip()
    return _tip


def reset_kws_miss_tip():
    """Drop the singleton for test isolation."""
    global _tip
    _tip = None
