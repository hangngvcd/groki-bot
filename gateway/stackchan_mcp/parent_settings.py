# SPDX-License-Identifier: MIT
"""Parent-owned settings; no protocol messages are invented here."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)
SCHEMA = {
    "battery_curve_raw": {"type": "array", "default": None},
    "battery_low_threshold_pct": {"type": "integer", "min": 5, "max": 50, "default": 20},
    "session_idle_min": {"type": "integer", "min": 1, "max": 240, "default": 30},
    "pin_minigame": {
        "type": "string", "default": "", "enum": ["", "G1", "G2", "G3"],
        "description_zh": "小遊戲固定款式（留空輪流）",
    },
    "volume": {
        "type": "integer",
        "min": 0,
        "max": 100,
        "default": 40,
        "description_zh": "音量",
    },
    "rest_after_min": {
        "type": "integer",
        "min": 1,
        "max": 60,
        "default": 5,
        "description_zh": "無互動後休息分鐘",
    },
    "camera_presence": {
        "type": "boolean",
        "default": False,
        "description_zh": "鏡頭在場偵測",
    },
    "notifications": {
        "type": "boolean",
        "default": False,
        "description_zh": "家長通知",
    },
    "mandarin": {
        "type": "boolean",
        "default": True,
        "description_zh": "容許鈴鈴應娜娜要求講普通話",
    },
}
TICKETS = {
    "pin_minigame": "30",
    "rest_after_min": "12/15",
    "camera_presence": "23",
    "notifications": "19",
}


# Settings the gateway sends to the device (ticket 15c: volume via
# {"type": "xiaoya", "cmd": "volume", "volume": N}, re-sent on every connect).
APPLIED = {
    "battery_curve_raw": "sent on session ready and when changed",
    "battery_low_threshold_pct": "sent on session ready and when changed",
    "volume": "sent to 鈴鈴 now if connected, otherwise on her next connect",
}


# Settings whose policy is applied by the gateway; device UI may need firmware.
GATEWAY = {
    "session_idle_min": "homework session idle safety timeout",
    "camera_presence": "Gateway desk presence policy (ticket 23); camera_on protocol stub, firmware icon pending",
    "mandarin": (
        "鈴鈴語言規則：進行中嘅對話即時收到通知，下次對話嘅 prompt 一定用新設定"
    ),
}


def device_support():
    support = {key: {"applied_to_device": True, "reason": reason} for key, reason in APPLIED.items()}
    support.update(
        {
            key: {"applied_to_device": False, "applied_in_gateway": True, "reason": reason}
            for key, reason in GATEWAY.items()
        }
    )
    support.update(
        {
            key: {
                "applied_to_device": False,
                "reason": f"stored; firmware has no message yet (ticket {ticket})",
            }
            for key, ticket in TICKETS.items() if key not in GATEWAY
        }
    )
    return support


def validate(values):
    if not isinstance(values, dict) or not values:
        raise ValueError("Expected one or more settings")
    for key, value in values.items():
        if key not in SCHEMA:
            raise ValueError(f"Unknown setting: {key}")
        rule = SCHEMA[key]
        if key == "battery_curve_raw":
            from .battery_calibration import validate_curve
            if value is not None:
                validate_curve(value)
            continue
        expected = {"boolean": bool, "integer": int, "string": str}[rule["type"]]
        if type(value) is not expected:
            raise ValueError(f"{key}: expected {rule['type']}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"{key}: invalid choice")
        if expected is int and not rule["min"] <= value <= rule["max"]:
            raise ValueError(f"{key}: expected {rule['min']}..{rule['max']}")
    return values


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


class ParentSettings:
    def __init__(self, path=None):
        self.path = Path(
            path
            or os.getenv(
                "STACKCHAN_PARENT_SETTINGS_PATH", str(Path(os.getenv("STACKCHAN_STATE_DIR", "~/.stackchan")) / "parent_settings.json")
            )
        ).expanduser()
        self._settings = {key: rule["default"] for key, rule in SCHEMA.items()}
        self._listeners = []
        self._pushers = {}
        self._tasks = set()
        try:
            data = validate(json.loads(self.path.read_text()))
            self._settings.update(data)
        except FileNotFoundError:
            logger.info("Parent settings not saved yet; using defaults: %s", self.path)
        except (OSError, ValueError, TypeError):
            logger.warning(
                "Parent settings missing or corrupt; using defaults: %s", self.path
            )

    def get(self):
        return deepcopy(self._settings)

    def add_listener(self, listener):
        self._listeners.append(listener)

    def remove_listener(self, listener):
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def register_pusher(self, key, pusher):
        if key not in SCHEMA:
            raise ValueError(f"Unknown setting: {key}")
        self._pushers[key] = pusher

    async def push_to_device(self, keys=None):
        for key in self._pushers if keys is None else keys:
            if key in self._pushers:
                try:
                    await self._pushers[key](self._settings[key])
                except Exception:
                    logger.exception("Parent setting device push failed: %s", key)

    def set(self, key, value, *, source):
        return self.update({key: value}, source=source)

    def update(self, values, source):
        validate(values)
        values = deepcopy(values)
        changed = {
            key: {"old": self._settings[key], "new": value}
            for key, value in values.items()
            if self._settings[key] != value
        }
        # A repeated explicit toggle still overrides the session's effective state.
        if "camera_presence" in values and "camera_presence" not in changed:
            for listener in self._listeners:
                try:
                    listener(self.get(), {"camera_presence"})
                except Exception:
                    logger.exception("Parent presence override listener failed")
        if not changed:
            return {"changed": {}, "settings": self.get()}
        settings = {**self._settings, **values}
        private_directory(self.path.parent)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".parent-settings-")
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(settings, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self._settings = settings
        for key, delta in changed.items():
            logger.info(
                "Parent setting changed: %s %s -> %s (source=%s)",
                key,
                delta["old"],
                delta["new"],
                source,
            )
        for listener in self._listeners:
            try:
                listener(self.get(), set(changed))
            except Exception:
                logger.exception("Parent settings listener failed")
        if self._pushers:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.push_to_device(tuple(changed)))
            else:
                task = loop.create_task(self.push_to_device(tuple(changed)))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return {"changed": changed, "settings": self.get()}


_settings = None


def get_parent_settings():
    global _settings
    if _settings is None:
        _settings = ParentSettings()
    return _settings


def mandarin_enabled():
    """Whether 鈴鈴 may switch to Mandarin when 娜娜 asks (default on)."""
    try:
        return bool(get_parent_settings().get().get("mandarin", SCHEMA["mandarin"]["default"]))
    except Exception:
        logger.debug("mandarin setting unreadable; using default", exc_info=True)
        return SCHEMA["mandarin"]["default"]


def reset_parent_settings():
    global _settings
    _settings = None
