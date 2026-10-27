"""Explicit homework photos, separate from metadata-only desk presence."""

from datetime import datetime
import logging
from pathlib import Path
import tempfile
import time
import threading

from PIL import Image, ImageStat

from .parent_settings import private_directory
from .stuck_notify import PARENT_HELPER_BOT, _send

GUIDE_LINE = "好嘢！擺份功課喺我前面俾我影低先。"
logger = logging.getLogger(__name__)


def resolve_item(claimed, unfinished_titles):
    titles = list(unfinished_titles)
    text = str(claimed or "").strip().casefold()
    matches = [t for t in titles if text and text == t.casefold()]
    if not matches:
        matches = [t for t in titles if text and text in t.casefold()]
    if len(matches) == 1:
        return {"status": "use", "item": matches[0]}
    if len(titles) == 1 and not text:
        return {"status": "use", "item": titles[0]}
    if not titles:
        return {"status": "none", "spoken": "今日清單冇未做嘅功課喇。"}
    return {"status": "ask", "spoken": "係邊樣做完？", "choices": titles[:3]}


def assess_photo(path):
    """Conservative exposure/blank check; alignment needs a board assessor."""
    try:
        if Path(path).stat().st_size < 256:
            return "blank"
        with Image.open(path) as image:
            image.thumbnail((160, 160))
            stat = ImageStat.Stat(image.convert("L"))
            if stat.mean[0] < 25:
                return "dark"
            return "blank" if stat.stddev[0] < 5 else "ok"
    except (OSError, TypeError, ValueError):
        return "blank"


class HomeworkPhoto:
    def __init__(
        self,
        *,
        forms,
        presence,
        capture=None,
        assess=assess_photo,
        send=_send,
        captures_dir=None,
    ):
        self.forms, self.presence = forms, presence
        self.capture = capture or self._unavailable
        self.assess, self.send = assess, send
        self.captures_dir = Path(
            captures_dir or "~/.stackchan/captures/homework"
        ).expanduser()
        self.item = None
        self.attempts = 0
        self.asked = False
        self.cleanup()

    @staticmethod
    def _unavailable():
        raise OSError("OV5640 capture adapter not connected")

    def cleanup(self):
        if self.captures_dir.exists():
            for path in self.captures_dir.glob("homework_*.jpg"):
                if path.is_file() and path.stat().st_mtime < time.time() - 86400:
                    path.unlink(missing_ok=True)

    def save_frame(self, jpeg):
        """Board adapter only: save one explicit snap, never a presence frame."""
        private_directory(self.captures_dir)
        self.cleanup()
        with tempfile.NamedTemporaryFile(
            prefix="homework_", suffix=".jpg", dir=self.captures_dir, delete=False
        ) as handle:
            handle.write(jpeg)
            path = handle.name
        timer = threading.Timer(86400, lambda: Path(path).unlink(missing_ok=True))
        timer.daemon = True
        timer.start()
        return path

    @staticmethod
    def message(phase):
        message = {"type": "xiaoya", "cmd": "homework_photo", "phase": phase}
        if phase == "shutter":
            message.update(sfx="sfx.shutter", face="face.camera")
        return message

    def resolve(self, claimed=None):
        if self.item is not None:
            return {"ok": True, "photo_pending": True, "item": self.item["title"]}
        items = [i for i in self.forms.snapshot()["items"] if not i["done"]]
        # IDs remain valid references, without exposing arbitrary paths to voice tools.
        for item in items:
            if str(claimed or "").lstrip("#") == str(item["id"]):
                claimed = item["title"]
        result = resolve_item(claimed, [i["title"] for i in items])
        if result["status"] == "use":
            return self.begin(next(i for i in items if i["title"] == result["item"]))
        if result["status"] == "ask":
            if self.asked:
                result["spoken"] = ""
            self.asked = True
        return {"ok": True, **result}

    def begin(self, item):
        self.item, self.attempts, self.asked = dict(item), 0, False
        self.presence.set_suppress("photo", True)
        return {
            "ok": True,
            "photo_pending": True,
            "item": item["title"],
            "spoken": GUIDE_LINE,
            "device_messages": [self.message("guide")],
        }

    def attempt_capture(self):
        if self.item is None:
            return {"ok": False, "error": "no_photo_pending"}
        self.attempts += 1
        path = None
        try:
            frame = self.capture()
            path = self.save_frame(frame) if isinstance(frame, bytes) else str(frame)
            quality = self.assess(path)
        except Exception:
            logger.warning("Homework capture failed", exc_info=True)
            quality = "blank"
        messages = [self.message("shutter")] if path else []
        if quality == "ok":
            result = self.complete(True, path)
        elif self.attempts < 3:
            result = {
                "ok": True,
                "photo_pending": True,
                "attempts": self.attempts,
                "quality": quality,
                "spoken": "再擺好份功課，光啲俾我影多次呀。",
            }
        else:
            result = self.complete(False)
        result["device_messages"] = messages + result.get("device_messages", [])
        return result

    def complete(self, has_photo, path=None):
        if self.item is None:
            return {"ok": False, "error": "no_photo_pending"}
        try:
            result = self.forms.mark_done(self.item["id"], source="child-photo")
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
            tip = (
                f"【鈴鈴提示】娜娜做完功課，請睇相。\n功課：{self.item['title']}\n"
                f"相：{path if has_photo else '冇相'}\n時間：{stamp}"
            )
            sent = False
            try:
                self.send(PARENT_HELPER_BOT, tip)
                sent = True
            except Exception:
                logger.warning("Homework parent send failed", exc_info=True)
            from .debug_status import get_debug_status

            usage = get_debug_status().usage
            if usage:
                usage.photo(sent and has_photo)
            return {
                "ok": True,
                "item": self.item["title"],
                "already_done": result["already_done"],
                "done": result["done"],
                "total": result["total"],
                "level_up": result["level_up"],
                "game_unlock": result.get("game_unlock"),
                "invite_spoken": "變身喇！嚟玩！" if result.get("game_unlock") else "",
                "local_game_handoff": "Say only 收到, end voice turn; gateway local-loop owner must call consume_invite_start after form animation. Never send_task or bank."
                if result.get("game_unlock")
                else "",
                "photo_sent": sent and has_photo,
                "parent_notified": sent,
                "no_photo": not has_photo,
                "photo_pending": False,
                "device_messages": [self.message("done")],
            }
        finally:
            self.item = None
            self.presence.set_suppress("photo", False)
            self.cleanup()

    def cancel(self):
        self.item, self.attempts, self.asked = None, 0, False
        self.presence.set_suppress("photo", False)
        return {
            "ok": True,
            "photo_pending": False,
            "device_messages": [self.message("cancel")],
        }


_photo = None


def get_homework_photo():
    global _photo
    from .xiaoya_forms import get_xiaoya_forms
    from .camera_presence import get_camera_presence

    if (
        _photo is None
        or _photo.forms is not get_xiaoya_forms()
        or _photo.presence is not get_camera_presence()
    ):
        _photo = HomeworkPhoto(forms=get_xiaoya_forms(), presence=get_camera_presence())
    return _photo
