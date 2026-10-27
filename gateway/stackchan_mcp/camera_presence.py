"""Desk presence policy: metadata only; detection frames never saved/uploaded.

No camera, image bytes, storage, network or LLM access. Timers are in memory.
The board camera detector supplies face hits; this module never starts a camera.
"""

from dataclasses import dataclass
import time
import math

LEAVE_ABSENCE_S = 120.0
ENABLE_GRACE_S = 10.0
WELCOME_CLIPS = tuple(f"vo.welcome_back.{i}" for i in range(1, 7))
JOKE_CLIPS = tuple(f"vo.joke.{i}" for i in range(1, 4))


def presence_message(on: bool) -> dict:
    return {"type": "xiaoya", "cmd": "camera_presence", "on": on}


@dataclass(frozen=True)
class PresenceAction:
    kind: str
    clip: str = ""
    spoken: str = ""


class CameraPresence:
    def __init__(self, *, clock=time.monotonic, settings_getter=lambda: {}):
        self.clock = clock
        self._on = False
        self._manual_off = False
        self._suppress = set()
        self._pending_redo = None
        self._index = 0
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
            if (set(message) <= {"type", "event", "running", "hits_enabled"} and
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

    def tick(self):
        if not self._on:
            return None
        if self._blocked():
            self._last_face = self.clock()
            return None
        if not self._away and self.clock() - self._last_face >= LEAVE_ABSENCE_S:
            self._away = True
            return PresenceAction("leave_tip", "vo.leave_wait.1")
        return None

    def note_face(self):
        if not self._on:
            return None
        now = self.clock()
        returning = self._away or now - self._last_face >= LEAVE_ABSENCE_S
        self._last_face = now
        self._away = False
        if not returning or self._blocked():
            return None
        if self._pending_redo:
            return self.consume_pending_redo()
        pool = WELCOME_CLIPS + JOKE_CLIPS
        clip = pool[self._index % len(pool)]
        self._index += 1
        return PresenceAction("welcome", clip)


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
