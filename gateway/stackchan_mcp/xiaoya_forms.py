# SPDX-License-Identifier: MIT
"""鈴鈴 growth forms (F1 小苗貓 → F4 星冠貓) driven by today's homework progress.

The gateway owns the decision; the device only displays what it is told.

Rules (ticket 15):
  * total == 0            -> F1
  * done * 3 >= total     -> F2
  * done * 3 >= total * 2 -> F3
  * done == total         -> F4
  * the form only goes up during a day; it resets to F1 at local midnight.
  * levels the device has not shown yet are animated in turn on the device
    (it receives ``from`` and plays from+1 .. form ~0.5 s apart, then one
    praise line).

Progress comes in through the parent API (``POST /parent/v1/homework-progress``)
or ``parent_cli homework-progress``.  State lives in
``~/.stackchan/xiaoya_forms.json`` (override: ``STACKCHAN_XIAOYA_FORMS_PATH``).

Device message (gateway -> device, XiaoZhi text frame)::

    {"type": "xiaoya", "cmd": "form", "form": 3, "from": 1, "animate": true}
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Awaitable, Callable

from .parent_settings import private_directory

logger = logging.getLogger(__name__)

FORM_NAMES_ZH = {1: "小苗貓", 2: "小翼貓", 3: "寶石貓", 4: "星冠貓"}
MAX_TASKS = 50

Sender = Callable[[dict[str, Any]], Awaitable[None]]


def form_for_progress(done: int, total: int) -> int:
    """Map homework progress to a growth form 1..4."""
    if total <= 0:
        return 1
    done = max(0, min(done, total))
    if done == total:
        return 4
    if done * 3 >= total * 2:
        return 3
    if done * 3 >= total:
        return 2
    return 1


def validate_progress(done: Any, total: Any) -> tuple[int, int]:
    if type(done) is not int or type(total) is not int:
        raise ValueError("done/total: expected integers")
    if not 0 <= total <= MAX_TASKS:
        raise ValueError(f"total: expected 0..{MAX_TASKS}")
    if not 0 <= done <= total:
        raise ValueError("done: expected 0..total")
    return done, total


def _today() -> str:
    return _dt.datetime.now().astimezone().date().isoformat()


INTRO_MESSAGE: dict[str, Any] = {"type": "xiaoya", "cmd": "intro", "force": True}


def form_message(form: int, previous: int, animate: bool) -> dict[str, Any]:
    return {
        "type": "xiaoya",
        "cmd": "form",
        "form": form,
        "from": previous,
        "animate": bool(animate and form > previous),
    }


class XiaoyaForms:
    def __init__(self, path: str | os.PathLike | None = None, today: Callable[[], str] = _today):
        self.path = Path(
            path or os.getenv("STACKCHAN_XIAOYA_FORMS_PATH", "~/.stackchan/xiaoya_forms.json")
        ).expanduser()
        self._today = today
        self._state = self._fresh(today())
        self._sender: Sender | None = None
        self._tasks: set[asyncio.Task] = set()
        self._midnight_task: asyncio.Task | None = None
        try:
            data = json.loads(self.path.read_text())
            self._state = self._load(data)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning("鈴鈴 form state corrupt; starting from F1: %s", self.path)

    # -- state ---------------------------------------------------------------

    @staticmethod
    def _fresh(date: str, announced: int = 1) -> dict[str, Any]:
        return {"date": date, "done": 0, "total": 0, "form": 1, "announced_form": announced}

    @staticmethod
    def _load(data: dict[str, Any]) -> dict[str, Any]:
        done, total = validate_progress(data["done"], data["total"])
        form = int(data["form"])
        announced = int(data.get("announced_form", 1))
        if not (1 <= form <= 4 and 1 <= announced <= 4) or not isinstance(data["date"], str):
            raise ValueError("bad form")
        return {
            "date": data["date"],
            "done": done,
            "total": total,
            "form": max(form, form_for_progress(done, total)),
            "announced_form": announced,
        }

    def _save(self) -> None:
        private_directory(self.path.parent)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".xiaoya-forms-")
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

    def _roll_day(self) -> bool:
        today = self._today()
        if self._state["date"] == today:
            return False
        logger.info("鈴鈴 form: new day %s, reset to F1", today)
        # announced_form stays: the device still shows yesterday's form until
        # the next push, which sends a silent F1.
        self._state = self._fresh(today, announced=self._state["announced_form"])
        self._save()
        return True

    def snapshot(self) -> dict[str, Any]:
        self._roll_day()
        form = self._state["form"]
        return {
            **self._state,
            "form_name_zh": FORM_NAMES_ZH[form],
            "device_connected": self._sender is not None,
        }

    # -- inputs --------------------------------------------------------------

    def update_progress(self, done: Any, total: Any, *, source: str) -> dict[str, Any]:
        done, total = validate_progress(done, total)
        self._roll_day()
        old = self._state["form"]
        new = max(old, form_for_progress(done, total))  # only up within a day
        self._state.update({"done": done, "total": total, "form": new})
        self._save()
        logger.info(
            "鈴鈴 homework progress %d/%d -> F%d (was F%d, source=%s)", done, total, new, old, source
        )
        self._schedule_push()
        return self.snapshot()

    def note_device_event(self, message: dict[str, Any]) -> None:
        if message.get("event") == "form_state":
            # Sent by the device once its session is ready: the safe moment to
            # (re)send the current form, after hello / MCP initialize.
            logger.info("鈴鈴 device reports form=%s", message.get("form"))
            self._schedule_push(force=True)

    # -- device output -------------------------------------------------------

    def register_sender(self, sender: Sender, push: bool = True) -> asyncio.Task | None:
        """Called on device connect.

        With ``push=False`` the current form is sent later, when the device
        reports ``form_state`` (session ready), so it never races the hello /
        MCP initialize handshake.
        """
        self._sender = sender
        self._start_midnight_loop()
        return self._schedule_push(force=True) if push else None

    def unregister_sender(self, sender: Sender | None = None) -> None:
        if sender is None or self._sender == sender:
            self._sender = None
            if self._midnight_task is not None:
                self._midnight_task.cancel()
                self._midnight_task = None

    def next_message(self, force: bool = False) -> dict[str, Any] | None:
        self._roll_day()
        form = self._state["form"]
        announced = self._state["announced_form"]
        if form == announced and not force:
            return None
        return form_message(form, announced, animate=form > announced)

    async def push(self, force: bool = False) -> bool:
        sender = self._sender
        if sender is None:
            return False
        message = self.next_message(force=force)
        if message is None:
            return True
        try:
            await sender(message)
        except Exception:
            logger.warning("鈴鈴 form push failed; will retry on reconnect", exc_info=True)
            return False
        self._state["announced_form"] = message["form"]
        self._save()
        return True

    async def replay_intro(self) -> bool:
        """Ask the device to play its self-intro clip (vo.intro.1) now."""
        sender = self._sender
        if sender is None:
            return False
        try:
            await sender(dict(INTRO_MESSAGE))
        except Exception:
            logger.warning("鈴鈴 intro request failed", exc_info=True)
            return False
        return True

    def _schedule_push(self, force: bool = False) -> asyncio.Task | None:
        if self._sender is None:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(self.push(force=force))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _start_midnight_loop(self) -> None:
        if self._midnight_task is not None and not self._midnight_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._midnight_task = loop.create_task(self._midnight_loop())

    async def _midnight_loop(self) -> None:
        while True:
            now = _dt.datetime.now().astimezone()
            tomorrow = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
            await asyncio.sleep(max(1.0, (tomorrow - now).total_seconds()))
            if self._roll_day():
                await self.push()


_forms: XiaoyaForms | None = None


def get_xiaoya_forms() -> XiaoyaForms:
    global _forms
    if _forms is None:
        _forms = XiaoyaForms()
    return _forms


def reset_xiaoya_forms() -> None:
    global _forms
    _forms = None
