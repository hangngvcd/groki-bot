# SPDX-License-Identifier: MIT
"""鈴鈴 growth forms (F1 小苗貓 → F4 星冠貓) computed from today's homework list.

The gateway owns the list and the decision; the device only displays what it
is told.  The parent assistant never reports progress: it only edits the list
when a parent adds / removes homework.  An item is counted as done when 娜娜
reports it done (child voice tool ``homework_done``, after she confirms).

Rules (ticket 15, 小芽UX 2026-10-24):
  * total == 0            -> F1
  * done * 3 >= total     -> F2
  * done * 3 >= total * 2 -> F3
  * done == total         -> F4
  * the form only goes up during a day: adding homework or a redo request
    never downgrades; removing homework recomputes and may go up.
  * the list and the form reset (F1, empty list) at local midnight.
  * levels the device has not shown yet are animated in turn on the device
    (it receives ``from`` and plays from+1 .. form ~0.5 s apart, then one
    praise line).  The device waits until it is back in the companion state
    (after the homework-complete turn, not in a break / mini-game).

State lives in ``~/.stackchan/xiaoya_forms.json`` (override:
``STACKCHAN_XIAOYA_FORMS_PATH``).

Device message (gateway -> device, XiaoZhi text frame)::

    {"type": "xiaoya", "cmd": "form", "form": 3, "from": 1, "animate": true}
    {"type": "xiaoya", "cmd": "volume", "volume": 40}

The device's ``form_state`` event (session ready) also carries boot telemetry:
``reset_reason`` (poweron / brownout / panic / task_wdt ...), ``volume`` and
``uptime_ms``; the gateway logs it and keeps the latest copy for the parent
status.  The parent volume setting (parent_settings ``volume``) is re-sent on
every session-ready, so the parent's choice wins over on-device changes.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .parent_settings import get_parent_settings, private_directory

logger = logging.getLogger(__name__)

FORM_NAMES_ZH = {1: "小苗貓", 2: "小翼貓", 3: "寶石貓", 4: "星冠貓"}
MAX_TASKS = 50
SESSION_IDLE_SECONDS = 30 * 60

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


MAX_TITLE = 80


def clean_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("title: expected text")
    title = " ".join(value.split())
    if not 1 <= len(title) <= MAX_TITLE:
        raise ValueError(f"title: expected 1..{MAX_TITLE} characters")
    return title


def _clean_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > MAX_TASKS:
        raise ValueError("items: bad list")
    items, seen = [], set()
    for entry in raw:
        item_id = entry["id"]
        if type(item_id) is not int or item_id < 1 or item_id in seen:
            raise ValueError("items: bad id")
        seen.add(item_id)
        if type(entry["done"]) is not bool:
            raise ValueError("items: bad done")
        items.append({"id": item_id, "title": clean_title(entry["title"]), "done": entry["done"]})
    return items


def _today() -> str:
    return _dt.datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()


# Spoken by the gateway (edge-tts) only when the device has no vo.intro.1
# clip; keep in sync with tools/lingling_assets/lines.json.
# Firmware flash is required for the ≤200ms BOOT chime / 350ms double-press window.
INTRO_TEXT = (
    "娜娜你好！我係鈴鈴，一隻會慢慢長大嘅白貓仔。"
    "你做功課嗰陣我會陪住你。叫我「鈴鈴」，或者撳一下我，我就聽你。"
)
INTRO_VOICE = "zh-HK-HiuMaanNeural"

INTRO_MESSAGE: dict[str, Any] = {"type": "xiaoya", "cmd": "intro", "force": True}


ABNORMAL_RESETS = {"brownout", "panic", "task_wdt", "int_wdt", "wdt"}


def volume_message(volume: int) -> dict[str, Any]:
    if type(volume) is not int or not 0 <= volume <= 100:
        raise ValueError("volume: expected 0..100")
    return {"type": "xiaoya", "cmd": "volume", "volume": volume}


def form_message(form: int, previous: int, animate: bool) -> dict[str, Any]:
    return {
        "type": "xiaoya",
        "cmd": "form",
        "form": form,
        "from": previous,
        "animate": bool(animate and form > previous),
    }


class XiaoyaForms:
    def __init__(
        self,
        path: str | os.PathLike | None = None,
        today: Callable[[], str] = _today,
        volume_source: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.path = Path(
            path or os.getenv("STACKCHAN_XIAOYA_FORMS_PATH", str(Path(os.getenv("STACKCHAN_STATE_DIR", "~/.stackchan")) / "xiaoya_forms.json"))
        ).expanduser()
        self._clock = clock
        self.session_active = False
        self._last_interaction = clock()
        self._session_timer = None
        self._copy_mode_said = False
        self._active_vocab_item = None
        self._today = today
        self._state = self._fresh(today())
        self._sender: Sender | None = None
        self._session_ready = False
        self._volume_source = volume_source or (lambda: get_parent_settings().get()["volume"])
        self.device_report: dict[str, Any] = {}
        self._tasks: set[asyncio.Task] = set()
        self._midnight_task: asyncio.Task | None = None
        # Live voice sessions subscribe so the model learns the list changed.
        self._change_listeners: list[Callable[[str, str], None]] = []
        try:
            data = json.loads(self.path.read_text())
            self._state = self._load(data)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning("鈴鈴 form state corrupt; starting from F1: %s", self.path)

    def session_message(self):
        return {"type": "xiaoya", "cmd": "session", "active": self.session_active}

    def start_homework_session(self):
        changed = not self.session_active
        self.session_active = True
        self.note_interaction()
        if changed:
            self._schedule(self.send_device(self.session_message()))

    def note_interaction(self):
        self._last_interaction = self._clock()
        if self._session_timer is not None:
            self._session_timer.cancel()
        if self.session_active:
            try:
                self._session_timer = asyncio.get_running_loop().call_later(
                    get_parent_settings().get()["session_idle_min"] * 60, self.check_session_timeout)
            except RuntimeError:
                pass

    def end_homework_session(self):
        if self._session_timer is not None:
            self._session_timer.cancel()
            self._session_timer = None
        if self.session_active:
            self.session_active = False
            self._schedule(self.send_device(self.session_message()))

    def check_session_timeout(self):
        if self.session_active and self._clock() - self._last_interaction >= get_parent_settings().get()["session_idle_min"] * 60:
            self.end_homework_session()

    # -- state ---------------------------------------------------------------

    @staticmethod
    def _fresh(date: str, announced: int = 1) -> dict[str, Any]:
        return {"date": date, "items": [], "next_id": 1, "form": 1, "announced_form": announced, "celebrated_all": False}

    @staticmethod
    def _load(data: dict[str, Any]) -> dict[str, Any]:
        # Files written before the homework list existed carry only
        # done/total; they load with an empty list and keep today's form.
        items = _clean_items(data.get("items", []))
        form = int(data["form"])
        announced = int(data.get("announced_form", 1))
        if not (1 <= form <= 4 and 1 <= announced <= 4) or not isinstance(data["date"], str):
            raise ValueError("bad form")
        if "items" not in data and "done" in data:
            done, total = data["done"], data["total"]
            if type(done) is not int or type(total) is not int or not 0 <= done <= total <= MAX_TASKS:
                raise ValueError("bad legacy progress")
            if form > max(form_for_progress(done, total), 1):
                raise ValueError("bad legacy form")
        next_id = max([data.get("next_id", 1) if type(data.get("next_id", 1)) is int else 1,
                       *(item["id"] + 1 for item in items)])
        done = sum(item["done"] for item in items)
        return {
            "date": data["date"],
            "items": items,
            "next_id": next_id,
            "form": max(form, form_for_progress(done, len(items))),
            "announced_form": announced,
            "celebrated_all": data.get("celebrated_all", False) is True,
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
        self._copy_mode_said = False
        self._active_vocab_item = None
        self._state = self._fresh(today, announced=self._state["announced_form"])
        self._save()
        return True

    @property
    def copy_mode_said(self) -> bool:
        self._roll_day()
        return self._copy_mode_said

    def set_vocab_context(self, item, *, copy_mode_said: bool = False) -> None:
        """Voice-only context: titles themselves never grant copy mode.

        Calling for a different item clears the previous assertion, including
        when returning to an item that previously allowed copying.
        """
        self._roll_day()
        try:
            active = self._find(item)
            if active["done"]:
                raise ValueError("Homework item is already done")
        except (ValueError, LookupError):
            self._copy_mode_said = False
            self._active_vocab_item = None
            raise
        key = (active["id"], active["title"])
        if key != self._active_vocab_item:
            self._copy_mode_said = False
        self._active_vocab_item = key
        if copy_mode_said:
            self._copy_mode_said = True

    def _counts(self) -> tuple[int, int]:
        items = self._state["items"]
        return sum(item["done"] for item in items), len(items)

    def snapshot(self) -> dict[str, Any]:
        self._roll_day()
        form = self._state["form"]
        done, total = self._counts()
        return {
            "date": self._state["date"],
            "items": [dict(item) for item in self._state["items"]],
            "done": done,
            "total": total,
            "form": form,
            "announced_form": self._state["announced_form"],
            "form_name_zh": FORM_NAMES_ZH[form],
            "device_connected": self._sender is not None,
            "device": dict(self.device_report),
        }

    # -- inputs --------------------------------------------------------------

    def _find(self, ref: Any) -> dict[str, Any]:
        """Resolve an item by id (3 / "3" / "#3"), exact title or unique substring."""
        items = self._state["items"]
        if type(ref) is int or (isinstance(ref, str) and ref.strip().lstrip("#").isdigit()):
            wanted = int(ref.strip().lstrip("#")) if isinstance(ref, str) else ref
            for item in items:
                if item["id"] == wanted:
                    return item
            raise LookupError(f"冇第 {wanted} 項功課 / no homework #{wanted}")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("item: expected id or title")
        text = " ".join(ref.split()).casefold()
        exact = [item for item in items if item["title"].casefold() == text]
        if len(exact) == 1:
            return exact[0]
        partial = [item for item in items if text in item["title"].casefold()]
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise LookupError(f"清單入面冇「{ref.strip()}」/ not on today's list")
        names = "、".join(f"#{item['id']} {item['title']}" for item in partial)
        raise LookupError(f"有幾項都似：{names} / ambiguous")

    def _commit(self, action: str, source: str) -> dict[str, Any]:
        self._copy_mode_said = False
        self._active_vocab_item = None
        old = self._state["form"]
        done, total = self._counts()
        new = max(old, form_for_progress(done, total))  # only up within a day
        self._state["form"] = new
        self._save()
        logger.info(
            "鈴鈴 homework %s %d/%d -> F%d (was F%d, source=%s)", action, done, total, new, old, source
        )
        self._schedule_push()
        self._notify_change(action, source)
        from .camera_presence import get_camera_presence
        presence = get_camera_presence()
        if total > 0 and done == total:
            presence.end_homework_day()
            if action != "done":
                self.end_homework_session()
        if action == "redo":
            presence.set_pending_redo("爸爸想你再試一次，我陪你呀。")
        unlock = None
        if new > old:
            from .xiaoya_games import get_xiaoya_games

            unlock = get_xiaoya_games().on_level_up(old, new)
        return {**self.snapshot(), "level_up": new > old, "previous_form": old,
                "game_unlock": unlock}

    def add_change_listener(self, callback: Callable[[str, str], None]) -> None:
        if callback not in self._change_listeners:
            self._change_listeners.append(callback)

    def remove_change_listener(self, callback: Callable[[str, str], None]) -> None:
        try:
            self._change_listeners.remove(callback)
        except ValueError:
            pass

    def _notify_change(self, action: str, source: str) -> None:
        for callback in list(self._change_listeners):
            try:
                callback(action, source)
            except Exception:
                logger.exception("鈴鈴 homework change listener failed")

    def add_items(self, titles: Any, *, source: str) -> dict[str, Any]:
        """Parent adds homework.  Never downgrades the form."""
        if isinstance(titles, str):
            titles = [titles]
        if not isinstance(titles, list) or not titles:
            raise ValueError("add: expected a list of titles")
        cleaned = [clean_title(title) for title in titles]
        self._roll_day()
        if len(self._state["items"]) + len(cleaned) > MAX_TASKS:
            raise ValueError(f"最多 {MAX_TASKS} 項 / at most {MAX_TASKS} items")
        for title in cleaned:
            self._state["items"].append({"id": self._state["next_id"], "title": title, "done": False})
            self._state["next_id"] += 1
        return self._commit("add", source)

    def remove_items(self, refs: Any, *, source: str) -> dict[str, Any]:
        """Parent removes homework; the form is recomputed (may go up, never down)."""
        if not isinstance(refs, list) or not refs:
            raise ValueError("remove: expected a list of ids or titles")
        self._roll_day()
        targets = {self._find(ref)["id"] for ref in refs}
        self._state["items"] = [item for item in self._state["items"] if item["id"] not in targets]
        return self._commit("remove", source)

    def redo_items(self, refs: Any, *, source: str, reason: str = "") -> dict[str, Any]:
        """Parent asks for a redo: the item is pending again, the form stays."""
        if not isinstance(refs, list) or not refs:
            raise ValueError("redo: expected a list of ids or titles")
        self._roll_day()
        targets = [self._find(ref) for ref in refs]
        for item in targets:
            item["done"] = False
        result = self._commit("redo", source)
        from .camera_presence import get_camera_presence
        line = f"爸爸睇過，想你再整好 {targets[0]['title']} 少少，我陪你一齊睇吓邊度要改。"
        reason = " ".join(str(reason or "").split())[:120]
        if reason and not any(word in reason for word in ("亂", "懶", "蠢", "粗心", "又錯", "失望")):
            line += f"可以一齊留意：{reason}。"
        get_camera_presence().set_pending_redo(line)
        return result

    def mark_done(self, ref: Any, *, source: str) -> dict[str, Any]:
        """娜娜 reports one item done (child voice tool, after she confirmed)."""
        self._roll_day()
        item = self._find(ref)
        already = item["done"]
        item["done"] = True
        result = self._commit("done", source)
        messages = []
        if not already:
            all_done = result["total"] > 0 and result["done"] == result["total"]
            level = "all" if all_done and not self._state["celebrated_all"] else "item"
            if level == "all":
                self._state["celebrated_all"] = True
                self._save()
            messages.append({"type": "xiaoya", "cmd": "celebrate", "level": level})
            self._schedule(self._send_completion(messages))
            # End locally now, but transmit false only after celebration.
            if all_done:
                if self._session_timer is not None:
                    self._session_timer.cancel()
                    self._session_timer = None
                was_active = self.session_active
                self.session_active = False
                if was_active:
                    messages.append(self.session_message())
        return {**result, "item": dict(item), "already_done": already,
                "device_messages": messages,
                "spoken": "你一路努力做，完成晒今日功課喇！" if messages and messages[0]["level"] == "all"
                else "你肯花心機完成呢一份，辛苦晒！"}

    async def _send_completion(self, messages):
        for message in messages:
            await self.send_device(message)

    def note_device_event(self, message: dict[str, Any]) -> None:
        if message.get("event") == "form_state":
            # Sent by the device once its session is ready: the safe moment to
            # (re)send the current form, after hello / MCP initialize.
            reason = str(message.get("reset_reason", "unknown"))[:16]
            report = {
                "form": message.get("form"),
                "volume": message.get("volume"),
                "reset_reason": reason,
                "uptime_ms": message.get("uptime_ms"),
                "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            # Ticket 15h: firmware's RTC-RAM panic breadcrumb from the previous
            # boot (kind, core, pc, task, backtrace, reason); absent if none.
            panic = message.get("panic")
            if isinstance(panic, str) and panic:
                report["panic"] = panic[:256]
            self.device_report = report
            if "panic" in report:
                logger.warning("鈴鈴 panic breadcrumb (previous boot): %s", report["panic"])
            log = logger.warning if reason in ABNORMAL_RESETS else logger.info
            log(
                "鈴鈴 device reports form=%s volume=%s reset_reason=%s uptime_ms=%s",
                report["form"], report["volume"], reason, report["uptime_ms"],
            )
            self._session_ready = True
            self._schedule_push(force=True)
            self._schedule(self.send_volume())
            from .battery_calibration import register_pushers
            settings = get_parent_settings()
            register_pushers(settings, self)
            self._schedule(settings.push_to_device(("battery_curve_raw", "battery_low_threshold_pct")))
            self.check_session_timeout()
            self._schedule(self.send_device(self.session_message()))

    # -- device output -------------------------------------------------------

    def register_sender(self, sender: Sender, push: bool = True) -> asyncio.Task | None:
        """Called on device connect.

        With ``push=False`` the current form is sent later, when the device
        reports ``form_state`` (session ready), so it never races the hello /
        MCP initialize handshake.
        """
        self._sender = sender
        self._session_ready = push
        self._start_midnight_loop()
        if push:
            self.check_session_timeout()
            self._schedule(self.send_device(self.session_message()))
        return self._schedule_push(force=True) if push else None

    def unregister_sender(self, sender: Sender | None = None) -> None:
        if sender is None or self._sender == sender:
            self._sender = None
            self._session_ready = False
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
        """Ask the device to replay its self-intro (vo.intro.1, or gateway TTS without the clip)."""
        sender = self._sender
        if sender is None:
            return False
        try:
            await sender(dict(INTRO_MESSAGE))
        except Exception:
            logger.warning("鈴鈴 intro request failed", exc_info=True)
            return False
        return True

    async def send_device(self, message: dict) -> bool:
        """Send a command only after the registered device session is ready."""
        sender = self._sender
        if sender is None or not self._session_ready:
            return False
        try:
            await sender(message)
        except Exception:
            logger.warning("鈴鈴 device command failed", exc_info=True)
            return False
        return True

    async def send_volume(self, volume: int | None = None) -> bool:
        """Send the parent volume (0..100) to the device once its session is ready.

        Registered as the parent-settings ``volume`` pusher, and called on every
        ``form_state`` so a rebooted device gets the parent's value back.
        """
        sender = self._sender
        if sender is None or not self._session_ready:
            return False
        try:
            message = volume_message(self._volume_source() if volume is None else volume)
            await sender(message)
        except Exception:
            logger.warning("鈴鈴 volume push failed; will retry on reconnect", exc_info=True)
            return False
        logger.info("鈴鈴 volume -> %d%%", message["volume"])
        return True

    @property
    def session_ready(self) -> bool:
        return self._sender is not None and self._session_ready

    def _schedule(self, coro) -> asyncio.Task | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return None
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _schedule_push(self, force: bool = False) -> asyncio.Task | None:
        if self._sender is None:
            return None
        return self._schedule(self.push(force=force))

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
            now = _dt.datetime.now(ZoneInfo("Asia/Hong_Kong"))
            tomorrow = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
            await asyncio.sleep(max(1.0, (tomorrow - now).total_seconds()))
            if self._roll_day():
                await self.push()


_forms: XiaoyaForms | None = None


def get_xiaoya_forms() -> XiaoyaForms:
    global _forms
    if _forms is None:
        _forms = XiaoyaForms()
        get_parent_settings().register_pusher("volume", _forms.send_volume)
    return _forms


def reset_xiaoya_forms() -> None:
    global _forms
    _forms = None
