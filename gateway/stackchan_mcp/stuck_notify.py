# SPDX-License-Identifier: MIT
"""Stuck-on-homework consent gate + one parent-helper notify per item/day (ticket 32)."""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import tempfile

from .gbot_client import GrokBotClient
from .parent_settings import private_directory

logger = logging.getLogger(__name__)

PARENT_HELPER_BOT = "05d81ca9-b265-4d98-b754-d3ecd4693095"
STUCK_THRESHOLD = 3
EMPATHY_LINE = "呢題真係唔易，卡住好正常。"
OFFER_LINE = "想小休一陣、先做第二樣，定係我話俾爸爸知你要幫手？"
SPOKEN_OK = "我話咗俾爸爸知"
SPOKEN_FAIL = "我記低咗，爸爸睇到就會知"
# Never spoken to the child; parent-helper only.
_TIP_PREFIX = "【鈴鈴提示】娜娜做功課卡住，想你幫手。"


def _send(bot, text):
    GrokBotClient().send(bot, text)


def _item_key(item) -> str:
    text = " ".join(str(item or "").split())
    if not text:
        raise ValueError("item: expected id or title")
    if len(text) > 80:
        text = text[:80]
    return text


def _point_key(point) -> str:
    if point is None:
        return ""
    text = " ".join(str(point).split())
    return text[:80]


def format_parent_tip(*, item: str, stuck_point: str = "", when: datetime | None = None) -> str:
    """Fixed parent-helper payload: no transcript, no photo."""
    stamp = (when or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M")
    point = stuck_point.strip() or "未講明"
    return (
        f"{_TIP_PREFIX}\n"
        f"功課：{item}\n"
        f"卡喺：{point}\n"
        f"時間：{stamp}"
    )


class StuckNotify:
    def __init__(self, path=None, *, today=None, send=None):
        self.path = Path(
            path
            or os.getenv("STACKCHAN_STUCK_NOTIFY_PATH", "~/.stackchan/stuck_notify.json")
        ).expanduser()
        self.today = today or (lambda: datetime.now().astimezone().date())
        self.send = send or _send
        self._state = {}
        try:
            raw = json.loads(self.path.read_text())
            if isinstance(raw, dict) and isinstance(raw.get("date"), str):
                items = raw.get("items")
                if isinstance(items, dict):
                    self._state = {"date": raw["date"], "items": items}
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logger.warning("Could not read stuck-notify state", exc_info=True)

    def _day_state(self):
        day = str(self.today())
        if self._state.get("date") != day or not isinstance(self._state.get("items"), dict):
            self._state = {"date": day, "items": {}}
        return self._state

    def _item_state(self, key: str) -> dict:
        items = self._day_state()["items"]
        entry = items.get(key)
        if not isinstance(entry, dict):
            entry = {
                "count": 0,
                "point": "",
                "notified": False,
                "last_offered_count": 0,
            }
            items[key] = entry
        entry.setdefault("count", 0)
        entry.setdefault("point", "")
        entry.setdefault("notified", False)
        entry.setdefault("last_offered_count", 0)
        if type(entry["count"]) is not int or entry["count"] < 0:
            entry["count"] = 0
        if type(entry["last_offered_count"]) is not int or entry["last_offered_count"] < 0:
            entry["last_offered_count"] = 0
        entry["notified"] = bool(entry["notified"])
        if not isinstance(entry["point"], str):
            entry["point"] = ""
        return entry

    def _persist(self) -> bool:
        temporary = None
        try:
            private_directory(self.path.parent)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.path.parent, delete=False, encoding="utf-8"
            ) as handle:
                temporary = Path(handle.name)
                json.dump(self._state, handle, ensure_ascii=False)
            temporary.replace(self.path)
            return True
        except OSError:
            logger.warning("Could not persist stuck-notify state", exc_info=True)
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def snapshot(self, item=None) -> dict:
        state = self._day_state()
        if item is None:
            return {
                "date": state["date"],
                "items": {
                    key: {
                        "count": int(entry.get("count") or 0),
                        "notified": bool(entry.get("notified")),
                        "point": str(entry.get("point") or ""),
                    }
                    for key, entry in state["items"].items()
                    if isinstance(entry, dict)
                },
            }
        key = _item_key(item)
        entry = self._item_state(key)
        return {
            "date": state["date"],
            "item": key,
            "count": entry["count"],
            "notified": entry["notified"],
            "point": entry["point"],
        }

    def note_stuck(self, item, point="", *, give_up=False) -> dict:
        """Record one stuck on the same homework item/point.

        Returns offer_help when the hint ladder has ~3 stucks (or give-up at 3+)
        and dad has not already been notified for this item today.
        """
        key = _item_key(item)
        entry = self._item_state(key)
        point_text = _point_key(point)
        if point_text:
            # New point on the same item: recount from this stuck.
            if entry["point"] and entry["point"] != point_text and not entry["notified"]:
                entry["count"] = 0
                entry["last_offered_count"] = 0
            entry["point"] = point_text
        if not give_up or entry["count"] < STUCK_THRESHOLD:
            entry["count"] += 1
        self._persist()
        return self._offer_result(key, entry, give_up=give_up)

    def _offer_result(self, key: str, entry: dict, *, give_up: bool) -> dict:
        count = entry["count"]
        notified = entry["notified"]
        # Offer once per item/day (anti-nag). give_up may re-speak the choice.
        should_offer = (
            count >= STUCK_THRESHOLD
            and not notified
            and (give_up or entry["last_offered_count"] == 0)
        )
        result = {
            "ok": True,
            "item": key,
            "count": count,
            "notified": notified,
            "offer_help": should_offer,
        }
        if should_offer:
            entry["last_offered_count"] = count
            self._persist()
            result["spoken"] = f"{EMPATHY_LINE}{OFFER_LINE}"
            result["empathy"] = EMPATHY_LINE
            result["offer"] = OFFER_LINE
        return result

    def notify_dad(self, item, stuck_point="", *, consented: bool) -> dict:
        """Send parent-helper tip only after 娜娜 consents. At most once per item/day."""
        key = _item_key(item)
        entry = self._item_state(key)
        point = _point_key(stuck_point) or entry.get("point") or ""

        if not consented:
            return {
                "ok": False,
                "sent": False,
                "error": "consent_required",
                "spoken": "要你同意我先話俾爸爸知㗎。",
            }
        if entry["count"] < STUCK_THRESHOLD:
            return {
                "ok": False,
                "sent": False,
                "error": "not_stuck_enough",
                "count": entry["count"],
                "spoken": "我再陪你試吓先。",
            }
        if entry["notified"]:
            return {
                "ok": True,
                "sent": False,
                "already_notified": True,
                "spoken": SPOKEN_OK,
            }

        tip = format_parent_tip(item=key, stuck_point=point)
        # Reserve before send so a flaky link cannot spam dad.
        entry["notified"] = True
        if point:
            entry["point"] = point
        if not self._persist():
            entry["notified"] = False
            return {"ok": False, "sent": False, "spoken": SPOKEN_FAIL}

        try:
            self.send(PARENT_HELPER_BOT, tip)
        except Exception:
            logger.warning("stuck-notify parent send failed", exc_info=True)
            return {"ok": True, "sent": False, "spoken": SPOKEN_FAIL}

        return {"ok": True, "sent": True, "spoken": SPOKEN_OK}


_stuck = None


def get_stuck_notify() -> StuckNotify:
    global _stuck
    if _stuck is None:
        _stuck = StuckNotify()
    return _stuck
