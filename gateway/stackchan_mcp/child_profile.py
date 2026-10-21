# SPDX-License-Identifier: MIT
"""Opt-in child policy. Restrictions override configuration without changing env."""

from __future__ import annotations

import os


VOICE_TOOL_NAMES = frozenset(
    {
        "send_task",
        "search_conversations",
        "end_the_call",
        "end_conversation",
    }
)

# Whitelisted faces: Neutral, Happy, Excited, Curious, Thinking, Affection,
# Surprised, Sad (empathy only). Allowed wire names are those faces plus the
# XiaoZhi aliases the firmware collapses onto them (main/conversation_task.cpp
# parse_emotion). Everything else (angry, bored, doubt, embarrassed->Doubt,
# confused, sleepy, ...) is sent as neutral.
EMOTION_NAMES = frozenset(
    {
        "neutral", "happy", "excited", "curious", "thinking", "affection", "surprised", "sad",
        # firmware aliases -> Excited / Happy / Sad / Surprised / Neutral
        "laughing", "funny",
        "loving", "delicious", "kissy", "winking", "silly",
        "crying",
        "shocked",
        "relaxed", "cool", "confident",
    }
)

# Defaults match the features' existing environment switches. Report defaults too:
# a clean environment otherwise autostarts the Mac camera and offers ask_claude.
_DISABLED_SETTINGS = {
    "STACKCHAN_MAC_CONTROL": "0",
    "STACKCHAN_ASK_CLAUDE": "1",
    "STACKCHAN_GEMINI_DEVICE_TOOLS": "0",
    "STACKCHAN_FACE_TRACKER_AUTOSTART": "1",
    "STACKCHAN_HEAD_FOLLOW_DEFAULT": "1",
    "STACKCHAN_USB_TRANSPORT": "0",
    "STACKCHAN_GATEWAY_IDLE_ENABLED": "0",
    "STACKCHAN_DEMO_REACTIONS": "0",
}


def enabled() -> bool:
    return os.getenv("STACKCHAN_CHILD_PROFILE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def voice_tool_allowed(name: str) -> bool:
    return not enabled() or name in VOICE_TOOL_NAMES


def filter_emotion(emotion: str) -> str:
    """Restrict device expressions in child mode; preserve the exact input otherwise."""
    if not enabled():
        return emotion
    value = str(emotion or "").strip().lower()
    return value if value in EMOTION_NAMES else "neutral"


def camera_tool_blocked(name: str) -> bool:
    return enabled() and (name == "take_photo" or name.startswith("self.camera."))


def backend_allowed(name: str) -> bool:
    # Cloud pass-through owns its own catalog; it cannot enforce this policy.
    return not enabled() or name in {"gbot", "gemini"}


def diagnostics() -> list[str]:
    """Safe to print in startup logs and --check; never include secret values."""
    if not enabled():
        return []
    messages = [
        f"{name}: conflicts with child profile; forced off"
        for name, default in _DISABLED_SETTINGS.items()
        if os.getenv(name, default).strip().lower()
        not in {"0", "false", "no", "off", ""}
    ]
    for name in ("VISION_HOST", "VISION_URL"):
        if os.getenv(name, "").strip():
            messages.append(
                f"{name}: conflicts with child profile; camera capture disabled"
            )
    if not backend_allowed(
        os.getenv("STACKCHAN_VOICE_BACKEND", "gemini").strip().lower()
    ):
        messages.append(
            "STACKCHAN_VOICE_BACKEND: unsupported in child profile; voice disabled (no fallback)"
        )
    if not os.getenv("STACKCHAN_TOKEN", "").strip():
        messages.append(
            "STACKCHAN_TOKEN: not set; child profile debug inject-text disabled"
        )
    messages.append("debug status: localhost-only; conversation text redacted")
    return messages
