"""Desk presence policy: metadata only; detection frames never saved/uploaded.

No camera or image access. Timers are in memory; optional tips use the parent helper.
The board camera detector supplies face hits; this module never starts a camera.
"""

from dataclasses import dataclass
import time
import math
import logging
import threading

from .stuck_notify import PARENT_HELPER_BOT, _send

logger = logging.getLogger(__name__)

LEAVE_ABSENCE_S = 120.0
ENABLE_GRACE_S = 10.0
WELCOME_CLIPS = tuple(f"vo.welcome_back.{i}" for i in range(1, 7))
JOKE_CLIPS = tuple(f"vo.joke.{i}" for i in range(1, 4))
PRAISE_CLIPS = tuple(f"vo.praise.{i}" for i in range(1, 5))
# Firmware falls back to .1 when an older pack lacks .2/.3.
LEAVE_CLIPS = tuple(f"vo.leave_wait.{i}" for i in range(1, 4))


def presence_message(on: bool) -> dict:
    return {"type": "xiaoya", "cmd": "camera_presence", "on": on}


@dataclass(frozen=True)
class PresenceAction:
    kind: str
    clip: str = ""
    spoken: str = ""


class ClipPool:
    """Round robin with at most five recent ids excluded when possible."""

    def __init__(self, clips):
        if not clips:
            raise ValueError("clip pool must be nonempty")
        self.clips = tuple(clips)
        self.cursor = 0
        self.recent = []
        self.window = min(5, len(self.clips) - 1)

    def next(self):
        for _ in range(len(self.clips)):
            clip = self.clips[self.cursor]
            self.cursor = (self.cursor + 1) % len(self.clips)
            if clip not in self.recent:
                break
        else:
            clip = self.clips[self.cursor]
            self.cursor = (self.cursor + 1) % len(self.clips)
        self.recent = ([clip] + self.recent)[:self.window]
        return clip


class CameraPresence:
    def __init__(self, *, clock=time.monotonic, settings_getter=lambda: {}, send=None):
        self.clock = clock
        self._settings_getter = settings_getter
        self._send = send or _send
        self._on = False
        self._manual_off = False
        self._suppress = set()
        self._pending_redo = None
        self._clips = ClipPool(WELCOME_CLIPS + JOKE_CLIPS)
        self._leave_clips = ClipPool(LEAVE_CLIPS)
        self._messages = []
        self._actions = []
        self._last_face = clock()
        self._grace_until = clock()
        self._away = False
        if settings_getter().get("camera_presence", False):
            self.on_parent_setting(True)

    def is_feature_on(self):
        return self._on

    def _enable(self, enabled):
        if enabled == self._on:
            return
        self._on = enabled
        self._last_face = self.clock()
        self._grace_until = self.clock() + ENABLE_GRACE_S
        self._away = False
        self._messages.append(presence_message(enabled))

    def start_homework_session(self):
        if not self._manual_off:
            self._enable(True)

    def end_homework_day(self):
        self._enable(False)
        self._manual_off = False

    def on_parent_setting(self, enabled):
        self._manual_off = not enabled
        self._enable(enabled)

    def drain_messages(self):
        messages, self._messages = self._messages, []
        return messages

    def set_suppress(self, reason, active):
        if active:
            self._suppress.add(reason)
        else:
            if reason in self._suppress:
                self._last_face = self.clock()
            self._suppress.discard(reason)

    def note_device_event(self, message):
        """Board lifecycle hook; begin/end events are firmware follow-ups."""
        if message.get("event") == "presence_status":
            confidence = message.get("confidence")
            if (set(message) <= {"type", "event", "present", "confidence"}
                    and type(message.get("present")) is bool
                    and type(confidence) in (int, float) and math.isfinite(confidence)
                    and 0.5 <= confidence <= 1.0):
                # A miss lets the existing absence grace expire; never reset it.
                if message["present"]:
                    action = self.note_face()
                    if action is not None:
                        self._actions.append(action)
            elif (set(message) <= {"type", "event", "running", "hits_enabled"} and
                    type(message.get("running")) is bool and type(message.get("hits_enabled")) is bool):
                self.set_suppress("camera_detector", not (message["running"] and message["hits_enabled"]))
        elif message.get("event") == "presence_hit":
            confidence = message.get("confidence")
            if (set(message) <= {"type", "event", "confidence"} and
                    type(confidence) in (int, float) and math.isfinite(confidence) and
                    0.5 <= confidence <= 1.0):
                action = self.note_face()
                if action is not None:
                    self._actions.append(action)
        elif message.get("event") == "presence_activity":
            reason = message.get("reason")
            active = message.get("active")
            if reason in {"photo", "evolve", "intro"} and type(active) is bool:
                self.set_suppress(reason, active)

    def drain_actions(self):
        actions, self._actions = self._actions, []
        return actions

    def set_pending_redo(self, spoken_or_clip=None):
        self._pending_redo = spoken_or_clip

    def consume_pending_redo(self):
        """Consume once on button/wake, or on a qualified desk return."""
        line, self._pending_redo = self._pending_redo, None
        if not line:
            return None
        return PresenceAction("redo", clip=line if line.startswith("vo.") else "",
                              spoken="" if line.startswith("vo.") else line)

    def is_suppressed(self):
        return self._blocked()

    def _blocked(self):
        return bool(self._suppress) or self.clock() < self._grace_until

    def _leave_seconds(self, settings):
        minutes = settings.get("presence_leave_min")
        if type(minutes) is int and 1 <= minutes <= 30:
            return minutes * 60
        return LEAVE_ABSENCE_S

    def _notify_away(self, minutes):
        """Transport and journaling run outside the device/event-loop thread."""
        sent = False
        try:
            self._send(PARENT_HELPER_BOT, f"【鈴鈴提示】娜娜行開咗 {minutes} 分鐘。")
            sent = True
        except Exception:
            # Exception text may contain the payload; deliberately omit it.
            logger.warning("presence-away parent send failed")
        try:
            from .parent_recovery import RecoveryStore

            RecoveryStore().presence_away_event(sent)
        except Exception:
            logger.warning("Could not persist presence-away recovery event")

    def tick(self):
        settings = self._settings_getter()
        leave_seconds = self._leave_seconds(settings)
        if not self._on:
            return None
        if self._blocked():
            self._last_face = self.clock()
            return None
        if not self._away and self.clock() - self._last_face >= leave_seconds:
            self._away = True
            if settings.get("notifications") is True:
                try:
                    threading.Thread(
                        target=self._notify_away, args=(int(leave_seconds / 60),), daemon=True
                    ).start()
                except Exception:
                    logger.warning("Could not start presence-away notification")
            return PresenceAction("leave_tip", self._leave_clips.next())
        return None

    def note_face(self):
        leave_seconds = self._leave_seconds(self._settings_getter())
        if not self._on:
            return None
        now = self.clock()
        returning = self._away or now - self._last_face >= leave_seconds
        self._last_face = now
        self._away = False
        if not returning or self._blocked():
            return None
        if self._pending_redo:
            return self.consume_pending_redo()
        clip = self._clips.next()
        return PresenceAction("joke" if clip in JOKE_CLIPS else "welcome", clip)


_presence = None


def get_camera_presence():
    global _presence
    if _presence is None:
        from .parent_settings import get_parent_settings

        settings = get_parent_settings()
        _presence = CameraPresence(settings_getter=settings.get)
        settings.add_listener(
            lambda values, changed: (
                _presence.on_parent_setting(values["camera_presence"])
                if "camera_presence" in changed
                else None
            )
        )
    return _presence
