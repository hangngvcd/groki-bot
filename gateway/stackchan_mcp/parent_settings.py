# SPDX-License-Identifier: MIT
"""Parent-owned settings; no protocol messages are invented here."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)
SCHEMA = {
    "volume": {
        "type": "integer",
        "min": 0,
        "max": 100,
        "default": 60,
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
}
TICKETS = {
    "volume": "16",
    "rest_after_min": "12/15",
    "camera_presence": "23",
    "notifications": "19",
}


def device_support():
    return {
        key: {
            "applied_to_device": False,
            "reason": f"stored; firmware has no message yet (ticket {ticket})",
        }
        for key, ticket in TICKETS.items()
    }


def validate(values):
    if not isinstance(values, dict) or not values:
        raise ValueError("Expected one or more settings")
    for key, value in values.items():
        if key not in SCHEMA:
            raise ValueError(f"Unknown setting: {key}")
        rule = SCHEMA[key]
        expected = bool if rule["type"] == "boolean" else int
        if type(value) is not expected:
            raise ValueError(f"{key}: expected {rule['type']}")
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
                "STACKCHAN_PARENT_SETTINGS_PATH", "~/.stackchan/parent_settings.json"
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
        return dict(self._settings)

    def add_listener(self, listener):
        self._listeners.append(listener)

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
        changed = {
            key: {"old": self._settings[key], "new": value}
            for key, value in values.items()
            if self._settings[key] != value
        }
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


def reset_parent_settings():
    global _settings
    _settings = None
