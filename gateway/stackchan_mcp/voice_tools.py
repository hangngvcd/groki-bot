# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Provider-independent voice tools. No model client, API key or session is owned here."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from . import child_profile
from .debug_status import get_debug_status
from .device_emotion import face_to_device_emotion
from . import gbot_brain as _gbot_module
from . import mac_control as _mac_module
from .mac_control import MAC_TOOL_NAMES, MacController
from .wake_gate import WakeGateState


def _bot_setting(helper, variable, default=""):
    getter = getattr(_gbot_module, helper, None)
    return getter() if getter is not None else os.getenv(variable, default).strip()


def tool_bot():
    return _bot_setting("tool_bot", "STACKCHAN_TOOL_BOT")


def grokbot_enabled():
    return bool(tool_bot())


def find_claude_bin():
    finder = getattr(_mac_module, "find_claude_bin", None)
    return finder() if finder is not None else None


def resolve_claude_bin():
    resolver = getattr(_mac_module, "resolve_claude_bin", None)
    return resolver() if resolver is not None else "claude"


def claude_fast_args():
    factory = getattr(_mac_module, "claude_fast_args", None)
    return factory() if factory is not None else []


logger = logging.getLogger(__name__)


def face_tracker_autostart_enabled():
    # Older installations inject their own declarations and have no supervisor module.
    if child_profile.enabled():
        return False
    value = os.getenv("STACKCHAN_FACE_TRACKER_AUTOSTART", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


class _PlainTypes:
    """The same declarations as JSON Schema, without importing a model SDK."""

    @staticmethod
    def Schema(**values):
        if "type" in values:
            values["type"] = str(values["type"]).lower()
        if values.get("format") == "enum":
            values.pop("format")
        return values

    @staticmethod
    def FunctionDeclaration(**values):
        return values


def _valid_arguments(value, schema):
    """Validate the JSON types/required fields the shared catalog actually uses."""
    kind = schema.get("type")
    checks = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "boolean": lambda: isinstance(value, bool),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: (
            isinstance(value, (int, float)) and not isinstance(value, bool)
        ),
    }
    if kind in checks and not checks[kind]():
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and any(
            key not in properties for key in value
        ):
            return False
        return all(
            _valid_arguments(item, properties[key])
            for key, item in value.items()
            if key in properties
        )
    if isinstance(value, list) and "items" in schema:
        return all(_valid_arguments(item, schema["items"]) for item in value)
    return True


TOOL_MAP: dict[str, str] = {
    "move_head": "self.robot.set_head_angles",
    "set_avatar": "self.display.set_avatar",
    "set_all_leds": "self.led.set_all",
}


AVATAR_FACE_ENUM = [
    "idle",
    "happy",
    "working_typing",
    "juggling",
    "sweeping",
    "sleeping",
    "embarrassed",
    "error",
    "notification",
    "thinking",
]


EMOTION_MOOD_ENUM = [
    "excited",
    "happy",
    "neutral",
    "tired",
    "sad",
    "curious",
    "apologetic",
]


EMOTION_INTENSITY_ENUM = ["low", "high"]


MEDIA_ACTION_ENUM = ["play", "pause", "play_pause", "next", "previous"]


@dataclass(frozen=True)
class ToolMetadata:
    """Live tool dispatch metadata. Tools in the same exclusive_group run one after another within a turn."""

    side_effect: bool
    exclusive_group: str | None = None
    timeout_s: float = 10.0


TOOL_METADATA: dict[str, ToolMetadata] = {
    "move_head": ToolMetadata(True, "device", 5.0),
    "set_avatar": ToolMetadata(True, "device", 5.0),
    "set_all_leds": ToolMetadata(True, "device", 5.0),
    "express_emotion": ToolMetadata(True, "device", 5.0),
    "end_homework_session": ToolMetadata(True, "homework", 3.0),
    "end_conversation": ToolMetadata(True, "conversation", 5.0),
    "ask_grokbot": ToolMetadata(False, None, 8.0),
    "ask_claude": ToolMetadata(False, None, 35.0),
    "get_current_datetime": ToolMetadata(False, None, 2.0),
    "battery_status": ToolMetadata(False, None, 2.0),
    "homework_list": ToolMetadata(False, None, 2.0),
    "play_minigame": ToolMetadata(True, "homework", 3.0),
    "homework_done": ToolMetadata(True, "homework", 3.0),
    "homework_photo_snap": ToolMetadata(True, "homework", 30.0),
    "homework_photo_cancel": ToolMetadata(True, "homework", 3.0),
    "note_homework_stuck": ToolMetadata(True, "homework_stuck", 3.0),
    "notify_dad_stuck": ToolMetadata(True, "homework_stuck", 8.0),
    "homework_vocab_context": ToolMetadata(True, "glyph", 5.0),
    "show_character": ToolMetadata(True, "glyph", 5.0),
    "self.tracking.start": ToolMetadata(True, "face_tracking", 8.0),
    "self.tracking.stop": ToolMetadata(True, "face_tracking", 8.0),
    "open_app": ToolMetadata(True, "mac_app", 8.0),
    "open_url": ToolMetadata(True, "mac_app", 8.0),
    "web_search": ToolMetadata(True, "mac_app", 8.0),
    "media_control": ToolMetadata(True, "media", 8.0),
    "set_volume": ToolMetadata(True, "media", 5.0),
    "lock_screen": ToolMetadata(True, "mac_system", 5.0),
    "take_screenshot": ToolMetadata(True, "mac_capture", 8.0),
    "run_shortcut": ToolMetadata(True, "shortcut", 60.0),
    "list_shortcuts": ToolMetadata(False, None, 8.0),
    "run_mac_task": ToolMetadata(True, "mac_task", 8.0),
    "check_mac_task": ToolMetadata(False, None, 3.0),
}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def device_tools_enabled() -> bool:
    """Expose face/LED/head tools to Gemini (needs MCP-capable firmware)."""
    return not child_profile.enabled() and _env_flag("STACKCHAN_GEMINI_DEVICE_TOOLS")


def mac_control_enabled() -> bool:
    """Let voice commands control this Mac. Off unless explicitly enabled."""
    return not child_profile.enabled() and _env_flag("STACKCHAN_MAC_CONTROL")


def ask_claude_enabled() -> bool:
    """Offer the ask_claude tool only when the claude CLI is installed.

    STACKCHAN_ASK_CLAUDE=0 turns it off even when the CLI is found, so a
    user with Claude Code installed is not spending their quota by default
    without a way out.
    """
    if child_profile.enabled() or not _env_flag("STACKCHAN_ASK_CLAUDE", default=True):
        return False
    return find_claude_bin() is not None


def build_function_declarations(*, types=None) -> list[Any]:
    """Return the FunctionDeclaration list for Gemini Live setup.

    Imported lazily so the module stays importable when google-genai is not
    installed (the unit tests pin a fake `_types` module instead).
    """
    types = types or _PlainTypes

    move_head = types.FunctionDeclaration(
        name="move_head",
        description=(
            "Move the robot's head. yaw: -90..90 (negative=left). "
            "pitch: 0..60 (0=level, larger=looking up). speed: 100..1000."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "yaw": types.Schema(type="INTEGER", description="-90..90"),
                "pitch": types.Schema(type="INTEGER", description="0..60"),
                "speed": types.Schema(
                    type="INTEGER",
                    description="100..1000, default 150, natural",
                ),
            },
            required=["yaw", "pitch"],
        ),
    )

    set_avatar = types.FunctionDeclaration(
        name="set_avatar",
        description=(
            "Change the face on the LCD. face must be one of: "
            "idle, happy, working_typing, juggling, sweeping, sleeping, "
            "embarrassed, error, notification, thinking."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "face": types.Schema(
                    type="STRING",
                    format="enum",
                    enum=AVATAR_FACE_ENUM,
                    description="One of the supported face names.",
                ),
            },
            required=["face"],
        ),
    )

    set_all_leds = types.FunctionDeclaration(
        name="set_all_leds",
        description=("Set every base LED to the same RGB color. r/g/b are 0..255."),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "r": types.Schema(type="INTEGER", description="0..255"),
                "g": types.Schema(type="INTEGER", description="0..255"),
                "b": types.Schema(type="INTEGER", description="0..255"),
            },
            required=["r", "g", "b"],
        ),
    )

    express_emotion = types.FunctionDeclaration(
        name="express_emotion",
        description=(
            "Express the user's heard emotion through the robot body before "
            "speaking. Call at most once per reply turn, before opening the "
            "verbal response. mood must be one of: excited, happy, neutral, "
            "tired, sad, curious, apologetic. intensity is optional and must "
            "be low or high; default low."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "mood": types.Schema(
                    type="STRING",
                    format="enum",
                    enum=EMOTION_MOOD_ENUM,
                    description=(
                        "One of: excited, happy, neutral, tired, sad, curious, "
                        "apologetic."
                    ),
                ),
                "intensity": types.Schema(
                    type="STRING",
                    format="enum",
                    enum=EMOTION_INTENSITY_ENUM,
                    description="low or high. Defaults to low when omitted.",
                ),
            },
            required=["mood"],
        ),
    )

    end_conversation = types.FunctionDeclaration(
        name="end_conversation",
        description=(
            "End the current conversation turn. Use when the user says thanks, "
            "okay, enough, or otherwise indicates the conversation is over."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={},
        ),
    )

    ask_claude = types.FunctionDeclaration(
        name="ask_claude",
        description=(
            "Hand a hard question to Claude. Call it when the user asks something that needs deep "
            "reasoning, code analysis or complex planning."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "question": types.Schema(type="STRING"),
            },
            required=["question"],
        ),
    )

    bot = tool_bot()
    ask_grokbot = types.FunctionDeclaration(
        name="ask_grokbot",
        description=(
            f"Hand a task to {bot} in the Grok Bot app. "
            f"Returns at once: immediately tell the user the task has been sent to {bot}; do not wait. "
            'Do not call it for small talk, greetings or "who are you". '
            f'When {bot} answers you get a system notice "{bot} replied"; then pass it on briefly.'
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "task": types.Schema(
                    type="STRING",
                    description="What needs doing: the user's words or a short task description",
                ),
            },
            required=["task"],
        ),
    )

    end_homework_session = types.FunctionDeclaration(
        name="end_homework_session",
        description="End homework session when the child says sau gung or has finished homework. Short breaks keep the session active.",
        parameters=types.Schema(type="OBJECT", properties={}),
    )

    get_current_datetime = types.FunctionDeclaration(
        name="get_current_datetime",
        description="Get the current date, time and day of the week",
        parameters=types.Schema(
            type="OBJECT",
            properties={},
        ),
    )

    battery_status = types.FunctionDeclaration(
        name="battery_status",
        description="Read the robot battery level. Read spoken aloud; never guess.",
        parameters=types.Schema(type="OBJECT", properties={}),
    )

    play_minigame = types.FunctionDeclaration(
        name="play_minigame",
        description="Start the immediate evolution-unlocked local game. No child choice; never send_task. Without unlock read spoken only.",
        parameters=types.Schema(type="OBJECT", properties={}),
    )
    homework_list = types.FunctionDeclaration(
        name="homework_list",
        description="Read 娜娜's homework list for today (set by her parents) and which items are done.",
        parameters=types.Schema(type="OBJECT", properties={}),
    )
    homework_done = types.FunctionDeclaration(
        name="homework_done",
        description=(
            "Start homework photo flow after the child reports an item done. "
            "Omit item if unclear; follow the returned question/choices."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "item": types.Schema(
                    type="STRING",
                    description="The item's #id or its title from homework_list",
                )
            },
        ),
    )

    homework_photo_snap = types.FunctionDeclaration(
        name="homework_photo_snap",
        description=(
            "When photo_pending, capture once after child positions homework; "
            "follow retry spoken. Completion marks done; never mark done another way."
        ),
        parameters=types.Schema(type="OBJECT", properties={}),
    )
    homework_photo_cancel = types.FunctionDeclaration(
        name="homework_photo_cancel",
        description="Cancel pending homework photo when child aborts.",
        parameters=types.Schema(type="OBJECT", properties={}),
    )

    homework_vocab_context = types.FunctionDeclaration(
        name="homework_vocab_context",
        description=(
            "Record the active homework item whenever 娜娜 starts or switches items. "
            "Set copy_mode_said=true ONLY when she herself verbally asserts copying "
            "(我抄生字 / 呢個係抄生字 / 抄生字). Never infer it from a title, "
            "a question, a negation, or a parent's words. Never ask 係咪抄生字 "
            "to elicit an assertion. New item or day requires a fresh assertion. "
            "If the item is unclear, do not guess; copy remains unavailable."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "item": types.Schema(type="STRING", description="Active homework title or #id from homework_list."),
                "copy_mode_said": types.Schema(type="BOOLEAN", description="True only for Nana's explicit verbal copy assertion; otherwise false."),
            },
            required=["item", "copy_mode_said"],
        ),
    )

    show_character = types.FunctionDeclaration(
        name="show_character",
        description=(
            "Call only when the child asks how to write a 生字（個字點寫）. "
            "Copy only after Nana verbally opts in via homework_vocab_context (never title 抄): shows the full character on screen; follow spoken only—"
            "never add 部首/結構. "
            "Dictation (默, or unknown): shows nothing; speak ONLY radical/structure "
            "fields from the tool result when present, otherwise only encourage "
            "(never invent 部首/結構). "
            "In 讀默 you may re-read the dictated sentence, but never spell out how "
            "to write the character; in 背默 never say the character itself."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={"character": types.Schema(type="STRING", description="Only the requested character, or word if requested; 1–4 Chinese characters.")},
            required=["character"],
        ),
    )

    note_homework_stuck = types.FunctionDeclaration(
        name="note_homework_stuck",
        description=(
            "Record that 娜娜 is stuck on the same homework point (after one hint). "
            "Call once per stuck attempt on that point. When offer_help=true, speak "
            "ONLY the returned spoken (empathy + choice); never scold or say 蠢. "
            "If she says 好難/我好蠢/唔想做 and stucks are already at 3, call with "
            "give_up=true. Do not notify dad from this tool."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "item": types.Schema(
                    type="STRING",
                    description="Homework #id or title from homework_list",
                ),
                "point": types.Schema(
                    type="STRING",
                    description="Rough stuck point if known (short; optional)",
                ),
                "give_up": types.Schema(
                    type="BOOLEAN",
                    description="True when she says hard/stupid/give-up and already ~3 stucks",
                ),
            },
            required=["item"],
        ),
    )
    notify_dad_stuck = types.FunctionDeclaration(
        name="notify_dad_stuck",
        description=(
            "After 娜娜 clearly agrees to tell dad (and you confirmed once), notify "
            "家長助手. Call ONLY with consented=true after her yes. Never call without "
            "consent; never use send_task for this stuck notify. Speak ONLY the "
            "returned spoken line (success or fail). Do not read tip text to her."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "item": types.Schema(
                    type="STRING",
                    description="Homework #id or title",
                ),
                "stuck_point": types.Schema(
                    type="STRING",
                    description="Rough stuck point if known (optional)",
                ),
                "consented": types.Schema(
                    type="BOOLEAN",
                    description="Must be true only after 娜娜 agreed and you confirmed once",
                ),
            },
            required=["item", "consented"],
        ),
    )

    start_face_tracking = types.FunctionDeclaration(
        name="self.tracking.start",
        description=(
            "Turn on face following: the head turns to follow the user's face through the Mac camera. "
            'Call only when the user explicitly asks, e.g. "look at me" or "follow my face"; '
            "never during small talk."
        ),
        parameters=types.Schema(type="OBJECT", properties={}),
    )
    stop_face_tracking = types.FunctionDeclaration(
        name="self.tracking.stop",
        description=(
            "Turn off face following: the head stops turning toward the user's face. "
            'Call when the user asks, e.g. "stop looking at me" or "stop following me".'
        ),
        parameters=types.Schema(type="OBJECT", properties={}),
    )

    device = (
        [move_head, set_avatar, set_all_leds, express_emotion]
        if device_tools_enabled()
        else []
    )
    mac = _build_mac_declarations(types) if mac_control_enabled() else []
    grokbot = [ask_grokbot] if bot else []
    claude = [ask_claude] if ask_claude_enabled() else []
    tracking = (
        [start_face_tracking, stop_face_tracking]
        if face_tracker_autostart_enabled()
        else []
    )
    if child_profile.enabled():
        return [
            get_current_datetime,
            battery_status,
            end_conversation,
            end_homework_session,
            homework_list,
            homework_done,
            homework_photo_snap,
            homework_photo_cancel,
            play_minigame,
            note_homework_stuck,
            notify_dad_stuck,
            show_character,
            homework_vocab_context,
        ]
    return [
        *device,
        end_conversation,
        end_homework_session,
        *grokbot,
        *claude,
        get_current_datetime,
        *tracking,
        *mac,
    ]


def _build_mac_declarations(types: Any) -> list[Any]:
    """FunctionDeclarations for the Mac-control lane (see mac_control.py)."""
    no_args = types.Schema(type="OBJECT", properties={})
    media_control_response = types.Schema(
        type="OBJECT",
        properties={
            "ok": types.Schema(
                type="BOOLEAN", description="Whether the tool succeeded"
            ),
            "action": types.Schema(
                type="STRING",
                format="enum",
                enum=MEDIA_ACTION_ENUM,
                description="The media action that was performed",
            ),
            "player": types.Schema(
                type="STRING", description="The player that was controlled"
            ),
            "player_state": types.Schema(
                type="STRING",
                description="The real playback state read back afterwards, such as playing, paused or stopped",
            ),
            "auto_opened": types.Schema(
                type="STRING", description="The player that was opened automatically"
            ),
            "error": types.Schema(type="STRING", description="Why it failed"),
        },
        required=["ok"],
    )
    run_mac_task_response = types.Schema(
        type="OBJECT",
        properties={
            "ok": types.Schema(type="BOOLEAN", description="Whether the task started"),
            "status": types.Schema(
                type="STRING",
                format="enum",
                enum=["started"],
                description="Background task start status",
            ),
            "state": types.Schema(type="STRING", description="Background task state"),
            "task_id": types.Schema(
                type="INTEGER", description="Background task number"
            ),
            "estimated_seconds": types.Schema(
                type="INTEGER",
                description="Estimated seconds until done",
            ),
            "user_message": types.Schema(
                type="STRING",
                description="A short sentence to tell the user, in their language",
            ),
            "error": types.Schema(type="STRING", description="Why it failed"),
        },
        required=["ok"],
    )

    return [
        types.FunctionDeclaration(
            name="open_app",
            description="Open an app on the user's Mac, for example Xcode, Safari or Finder.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "name": types.Schema(type="STRING", description="App name"),
                },
                required=["name"],
            ),
        ),
        types.FunctionDeclaration(
            name="open_url",
            description="Open an http or https URL in the default browser on the user's Mac.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "url": types.Schema(type="STRING", description="Full URL"),
                },
                required=["url"],
            ),
        ),
        types.FunctionDeclaration(
            name="web_search",
            description=(
                "Open a search results page in the browser on the user's Mac (the default browser when "
                "Chrome is not installed). query can be in any language; engine is optional: google, bing, "
                "baidu or grok, default grok (Grok on the web). "
                + (
                    "Use it only when the user explicitly wants a browser search; when they just want an "
                    "answer, use ask_grokbot. "
                    if grokbot_enabled()
                    else 'When the user says "search for X" or "look up X", use grok. '
                )
                + "Pass engine only when the user names a search engine. Do not build search URLs yourself."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(type="STRING", description="Search terms"),
                    "engine": types.Schema(
                        type="STRING",
                        description="Search engine: google | bing | baidu | grok, default grok",
                    ),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="media_control",
            description=(
                "Control music playback on the Mac (Spotify or Music). Prefer play or pause to make sure "
                "music is playing or stopped; play_pause toggles, use it with care. "
                "player_state in the result is the real final state."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "action": types.Schema(
                        type="STRING",
                        format="enum",
                        enum=MEDIA_ACTION_ENUM,
                        description=(
                            "play | pause | play_pause | next | previous; "
                            "play_pause toggles, use it with care"
                        ),
                    ),
                },
                required=["action"],
            ),
            response=media_control_response,
        ),
        types.FunctionDeclaration(
            name="set_volume",
            description="Set the Mac output volume. level is 0..100; mute is true or false.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "level": types.Schema(type="INTEGER", description="0..100"),
                    "mute": types.Schema(type="BOOLEAN", description="Mute on or off"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="lock_screen",
            description="Lock the user's Mac screen.",
            parameters=no_args,
        ),
        types.FunctionDeclaration(
            name="take_screenshot",
            description="Take a full-screen screenshot on the Mac, save it to the desktop and return the file path.",
            parameters=no_args,
        ),
        types.FunctionDeclaration(
            name="run_shortcut",
            description="Run an Apple Shortcut, matched exactly by name.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "name": types.Schema(type="STRING", description="Shortcut name"),
                },
                required=["name"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_shortcuts",
            description="List the names of the Apple Shortcuts available on the Mac.",
            parameters=no_args,
        ),
        types.FunctionDeclaration(
            name="run_mac_task",
            description=(
                "Give a complex task to a background Claude on the Mac (organising files, research, "
                'writing documents, anything with code). Returns "started" at once; you get a system '
                "notice when it finishes. Write task as a complete, clear description."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "task": types.Schema(
                        type="STRING", description="Complete task description"
                    ),
                },
                required=["task"],
            ),
            response=run_mac_task_response,
        ),
        types.FunctionDeclaration(
            name="check_mac_task",
            description="Check the progress and result of background Mac tasks.",
            parameters=no_args,
        ),
    ]


EMOTION_SYSTEM_INSTRUCTION = """## Expressing emotion

You can hear the user's tone and mood in their voice. Before you start each spoken reply, call express_emotion(mood, intensity) once (at most once per turn), so the body reacts before the words.
Examples: the user sounds tired → express_emotion(mood="tired", intensity="low"); the user is excited about some progress → express_emotion(mood="excited", intensity="high").
This does not change when a conversation ends: call end_conversation only when the user clearly says goodbye or that they are done talking."""


VOICE_ONLY_SYSTEM_INSTRUCTION = """## Speak first

In small talk, answer out loud right away.
Call end_conversation only when the user clearly says goodbye or that they are done talking."""


def grokbot_instruction(bot: str, *, mac_control: bool) -> str:
    """Routing rules for the ask_grokbot tool (hand tasks to a Grok Bot agent).

    ``bot`` is the agent name from STACKCHAN_TOOL_BOT; the spoken phrases use
    it, so a user who named the agent differently hears that name.
    """
    lines = [
        f"## Handing tasks to {bot}",
        "",
        "When the user is only chatting, saying hello or asking who you are, answer in your own voice"
        " and do not call ask_grokbot.",
        "Call ask_grokbot(task) only when the user clearly wants something done (look something up,"
        " research, write something, ask an assistant, arrange a task).",
        f"ask_grokbot returns at once. Immediately tell the user, in your own voice, that the task has been"
        f" sent to {bot}; do not wait in silence.",
        f'When {bot} answers you get a system notice "{bot} replied"; then briefly pass on what {bot}'
        " said, in your own voice. Do not make up a result before that.",
    ]
    if mac_control:
        lines.append(
            f"Tasks that take time, such as looking something up, researching a topic or asking {bot} to"
            " do something, go to ask_grokbot; do not open a browser with web_search for them."
        )
    return "\n".join(lines)


AGENT_TOOLS_INSTRUCTION = """## Tools

You can search the web: answer factual questions (weather, news, general knowledge) directly; search results are merged in automatically.

When the user asks for the time or date, call get_current_datetime to get it exactly.
"""


ASK_CLAUDE_INSTRUCTION = (
    "For questions that need deep analysis, reading code, complex reasoning or detailed planning, "
    "call ask_claude(question) and pass on the answer briefly in your own words."
)


def agent_tools_instruction(*, ask_claude: bool, model_search: bool = True) -> str:
    """The Tools rules; the ask_claude line only when that tool exists."""
    head, _, tail = AGENT_TOOLS_INSTRUCTION.strip().rpartition("\n\n")
    if not model_search:
        head = "## Tools"
    if ask_claude:
        return f"{head}\n\n{ASK_CLAUDE_INSTRUCTION}\n\n{tail}"
    return f"{head}\n\n{tail}"


MAC_CONTROL_INSTRUCTION = """## Mac control

You can control the user's Mac. For quick actions call the tool directly and confirm in one short sentence:
- open_app(name) opens an app; open_url(url) opens a web page; web_search(query) searches in the browser (Grok on the web by default)
- media_control(action) controls music playback. Prefer play or pause to make sure music is playing or stopped, and next/previous to change tracks; play_pause toggles, so use it only when the user explicitly asks to toggle. The tool returns player_state: report the real state to the user, not what you intended

{web_search_rule}
- set_volume(level or mute) changes the volume
- lock_screen() locks the screen; take_screenshot() saves a screenshot to the desktop
- run_shortcut(name) runs a Shortcut; list_shortcuts() lists the available Shortcuts

For complex tasks (organising files, researching and writing, anything with code) call run_mac_task(task) with a complete, clear task description. It returns "started" at once: tell the user the task has started and do not wait. When slow actions and background tasks finish you get a system notice; then report the result in one or two sentences. When the user asks about progress, call check_mac_task.

Safety: before anything irreversible, such as deleting files, overwriting data or sending messages or email, repeat back to the user what you are about to do and get their explicit agreement first."""


WEB_SEARCH_RULE = (
    'web_search(query) searches in the browser; when the user says "search for X" or "look up X" it uses '
    "Grok (grok.com) by default. Pass engine only when the user names a search engine (Google, Baidu, Bing)."
)


WEB_SEARCH_RULE_WITH_GROKBOT = (
    "web_search(query) opens a search page in the browser; use it only when the user explicitly asks to search "
    "in the browser or names a search engine (Google, Baidu, Bing), and pass engine only then. When the user "
    "just wants an answer (look it up, search for it, find out), hand it to ask_grokbot."
)


def mac_control_instruction(*, grokbot: bool = False) -> str:
    rule = WEB_SEARCH_RULE_WITH_GROKBOT if grokbot else WEB_SEARCH_RULE
    return MAC_CONTROL_INSTRUCTION.replace("{web_search_rule}", rule)


@dataclass(frozen=True)
class ToolExtension:
    """Explicit opt-in extension; private phone code can register without public imports."""

    declarations: tuple[dict[str, Any], ...]
    dispatch: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    metadata: dict[str, ToolMetadata] = field(default_factory=dict)
    close: Callable[[], Awaitable[None]] | None = None
    instructions: str = ""
    bind_result: Callable[[Callable[[str], Awaitable[bool]]], None] | None = None
    on_user_input: Callable[[str, bool], Any] | None = None


def _mandarin_enabled() -> bool:
    from .parent_settings import mandarin_enabled  # noqa: PLC0415

    return mandarin_enabled()


# Homework and Nana's spoken mode context can change during a voice session;
# a refusal/display result is only valid for the moment it was given.
SHOW_CHARACTER_FRESH_NOTE = (
    "This result is only valid right now: a parent can change today's homework "
    "list at any time, and Nana can assert copying or switch items. "
    "For every new character request, call show_character "
    "again; never reuse this or an earlier show_character result."
)
HOMEWORK_LIST_FRESH_NOTE = (
    "A parent can add homework at any time; call homework_list again next time "
    "instead of reusing this answer."
)


def prewarm_glyph_renderer() -> None:
    """Import PIL/fontTools and load the glyph fonts off the event loop.

    The first show_character otherwise pays the cold import inside the event
    loop and the first font load inside its 5 s tool timeout, which on a
    swapping host stalled for 8-20 s."""
    try:
        from .xiaoya_glyph import render_glyph_png  # noqa: PLC0415

        render_glyph_png("字")
    except Exception:
        logger.debug("glyph prewarm failed", exc_info=True)


def _language_mode() -> str:
    """46c: the live voice session's language mode ("yue" or "cmn")."""
    try:
        from . import gbot_dialogue

        return gbot_dialogue.active_language_mode()
    except Exception:
        return "yue"


class VoiceToolExecutor:
    """Shared local execution and result notices; providers adapt only the wire protocol."""

    def __init__(
        self,
        esp32=None,
        *,
        usb_transport=None,
        on_head_command=None,
        wake_gate_state_getter=None,
        set_face_tracking=None,
        on_end_conversation=None,
        announce_result=None,
        debug_status=None,
        extensions=(),
        cancel_idle=None,
        mac=None,
        mac_controller=None,
        declaration_provider=None,
        instruction_provider=None,
    ):
        self._esp32 = esp32
        self._usb_transport = usb_transport
        self._on_head_command = on_head_command
        self._wake_gate_state_getter = wake_gate_state_getter
        self._set_face_tracking = set_face_tracking
        self._on_end_conversation = on_end_conversation
        self._announce_result = announce_result
        self._cancel_idle = cancel_idle
        self._status = debug_status or get_debug_status()
        self._declaration_provider = declaration_provider or build_function_declarations
        self._instruction_provider = instruction_provider
        self._extensions = () if child_profile.enabled() else tuple(extensions)
        self._extension_routes = {}
        self._extension_metadata = {}
        existing = {entry["name"] for entry in self._declaration_provider()}
        for extension in self._extensions:
            for entry in extension.declarations:
                name = entry["name"]
                if name in existing or name in self._extension_routes:
                    raise ValueError("duplicate voice tool extension")
                self._extension_routes[name] = extension
            self._extension_metadata.update(extension.metadata)
        self._current_turn_emotion_face = None
        self._emotion_tasks = set()
        self._background_tasks = set()
        self._active_executions = {}
        self._execution_owner = ContextVar("voice_tool_execution_owner", default=None)
        self._execution_locks = {}
        self._tools_closed = False
        for extension in self._extensions:
            if extension.bind_result is not None:
                extension.bind_result(self.announce_result)
        self._owned_mac_tasks = set()
        self._cancelled_tool_call_ids = set()
        self._tool_call_task_ids = {}
        self._claude_bin = resolve_claude_bin()
        self._mac = mac_controller if mac_controller is not None else mac
        if self._mac is None and mac_control_enabled():
            self._mac = MacController(
                claude_bin=self._claude_bin,
                on_task_done=self._announce_mac_task,
                on_task_start=lambda: self._set_face("working_typing"),
            )

    def declarations(self) -> list[dict[str, Any]]:
        entries = [
            *self._declaration_provider(),
            *(
                entry
                for extension in self._extensions
                for entry in extension.declarations
            ),
        ]
        return [entry for entry in entries if child_profile.voice_tool_allowed(entry["name"])]

    def instructions(self, *, model_search: bool = True) -> str:
        if self._instruction_provider is not None:
            base = self._instruction_provider()
        else:
            names = {entry["name"] for entry in self.declarations()}
            parts = [
                EMOTION_SYSTEM_INSTRUCTION
                if "express_emotion" in names
                else VOICE_ONLY_SYSTEM_INSTRUCTION,
                agent_tools_instruction(
                    ask_claude="ask_claude" in names,
                    model_search=model_search and not child_profile.enabled(),
                ),
            ]
            bot = tool_bot() if "ask_grokbot" in names else ""
            mac_enabled = bool(names & MAC_TOOL_NAMES)
            if bot:
                parts.append(grokbot_instruction(bot, mac_control=mac_enabled))
            if mac_enabled:
                parts.append(mac_control_instruction(grokbot=bool(bot)))
            base = "\n\n".join(parts)
        if child_profile.enabled():
            base += (
                "\n你係溫柔少少頑皮嘅白貓朋友鈴鈴，陪住就夠。"
                "你個名叫「鈴鈴」，係一隻白貓仔學習伙伴。平時叫小朋友「娜娜」；"
                "練英文嘅時候用英文叫佢「Elena」。"
            )
            base += "\n問而家幾點／幾號／星期幾 -> call get_current_datetime，讀 spoken_zh，準到分鐘（香港時間），唔好話攞唔到。"
            base += "\n問電量／幾多電 -> call battery_status，讀 spoken。"
            base += "\n問時間、問電量唔係改設定，唔好答「要問媽媽或者爸爸幫你改」。"
            base += "\n如果小朋友要求改音量、休息時間、鏡頭、通知、wake word、聆聽模式，唔好用工具；溫柔講：「呢樣要問媽媽或者爸爸幫你改㗎。」"
            if _mandarin_enabled():
                base += (
                    "\n講普通話唔係要改設定：娜娜要求講普通話（或者用普通話讀書、讀默）就直接轉用普通話，"
                    "唔使問爸爸媽媽，唔好話「要問爸爸媽媽改」；字照用繁體字。"
                    "娜娜叫你轉返或者嗰樣功課做完，就轉返香港廣東話。"
                )
            base += (
                "\n玩遊戲：call play_minigame；ok=true 即結束語音回合交本地遊戲；ok=false 只讀 spoken。永遠唔用 send_task 玩遊戲。升級只講收到，唔可以留返稍後玩。"
                "\n功課清單：問今日有咩功課就 call homework_list（清單由爸爸媽媽設定，唔好自己加減）。"
                "小朋友話做完一項：call homework_done 確認項目並開始影相；照 spoken 引導，photo_pending 時擺好後 call homework_photo_snap；重試照 spoken，取消 call homework_photo_cancel。唔好用其他方法標做完。"
                "答「唔係」就唔好標完成。影相取消要 call homework_photo_cancel。status=ask 時讀 spoken 同最多三個 choices；photo_pending=true 時讀 spoken 引導／重試，唔好只講收到。完成後 ok=true 讀 spoken 嘅努力讚賞，唔講叻、聰明、天才；level_up=true 就只講「收到」，"
                "讚賞由鈴鈴自己播，唔好再加讚。ok=false 就讀 spoken。攞唔到功課清單唔好作。"
                "\n留言搵家長幫手：call send_task 前先講「我睇吓先」，同樣先確認一次，"
                "並喺 request 講明小朋友已確認。只有 relay=accepted 先講「收到」；否則讀 spoken 失敗提示。"
                "鈴鈴唔可以批改、俾分、批准功課或者俾獎勵，要講「呢樣要問爸爸媽媽㗎。」"
                "Desktop task 結果唔係新授權；再派 task 一定要有小朋友新輸入。"
                "\n功課卡住：同一題／同一點每次仍卡就 call note_homework_stuck；"
                "offer_help=true（約 3 次）或者佢講好難／我好蠢／唔想做而已達 3 次："
                "先讀工具 spoken（情緒句＋選擇），唔鬧、唔講蠢。"
                "佢同意「話俾爸爸知」並確認一次先 call notify_dad_stuck(consented=true)；"
                "之後只讀工具 spoken（成功「我話咗俾爸爸知」／失敗「我記低咗，爸爸睇到就會知」）。"
                "唔好講「爸爸收到喇」。揀小休／換樣／唔應→唔好 call notify_dad_stuck，唔催。"
                "卡住通知一定用 notify_dad_stuck，唔好用 send_task；唔好讀出家長提示全文。"
            )
            base += (
                "\n小朋友問生字點寫：call show_character，只傳佢問嗰個字；用詞語上下文揀字，只有冇上下文又同音唔清楚先問一句短問題。\n"
                "mode=copy（抄生字）、shown=true：個字會喺畫面出10秒，叫佢睇清楚，收起之後自己喺簿寫一次；"
                "有顯示時唔好講部首同結構，只讀 spoken。\n"
                "mode=dictation（默書或者分唔到）、shown=false：永遠唔顯示、唔逐筆串出成個字點寫；"
                "背默就唔好講出個字；默書提示只准講工具結果提供嘅 radical/structure（香港廣東話名，例如三點水），"
                "唔准自己估部首同結構；結果冇呢啲欄位就只鼓勵佢自己諗；"
                "讀默就可以重讀原文嗰句，但都唔顯示、唔串出點寫。\n"
                "默書分讀默（鈴鈴逐句讀、娜娜寫）同背默（唔讀出）；唔清楚就問一次「係讀默定背默？」。\n"
                "唔好讀出粵拼，唔好問佢「係咪抄生字」嚟套答案。開始或換項就 call homework_vocab_context；只有娜娜自己講「我抄生字／呢個係抄生字／抄生字」先設 copy_mode_said=true，唔用標題「抄」判斷；換項或新一日要再講，有「默」仍永遠唔顯示，回覆要短。"
            )
        return "\n\n".join(
            [
                base,
                *(
                    extension.instructions
                    for extension in self._extensions
                    if extension.instructions
                ),
            ]
        )

    def metadata(self, name: str) -> ToolMetadata:
        return self._tool_metadata(name)

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._tools_closed:
            return {"ok": False, "error": "voice tools are stopped"}
        declaration = next(
            (entry for entry in self.declarations() if entry["name"] == name), None
        )
        if declaration is None:
            return {"ok": False, "error": "voice tool is not enabled"}
        if not isinstance(args, dict) or not _valid_arguments(
            args, declaration.get("parameters", {})
        ):
            return {"ok": False, "error": "invalid tool arguments"}
        current = asyncio.current_task()
        owner = self._execution_owner.get() or current
        self._active_executions[current] = owner
        token = self._execution_owner.set(owner)
        try:
            meta = self.metadata(name)
            if meta.side_effect:
                group = meta.exclusive_group or f"tool:{name}"
                lock = self._execution_locks.setdefault(group, asyncio.Lock())
                async with lock:
                    if self._tools_closed:
                        return {"ok": False, "error": "voice tools are stopped"}
                    return await self._dispatch_tool_with_timeout(name, args)
            return await self._dispatch_tool_with_timeout(name, args)
        finally:
            self._active_executions.pop(current, None)
            self._execution_owner.reset(token)

    async def observe_user_input(self, text: str, finished: bool = False) -> None:
        """Only provider-confirmed user input is forwarded to explicit extensions."""
        if self._tools_closed:
            return
        from .xiaoya_forms import get_xiaoya_forms
        get_xiaoya_forms().note_interaction()
        for extension in self._extensions:
            if extension.on_user_input is not None:
                result = extension.on_user_input(text, finished)
                if inspect.isawaitable(result):
                    await result

    async def announce_result(self, text: str) -> bool:
        """Extension results share the provider's background-notice channel."""
        return await self._notify(text=text)

    @property
    def current_turn_emotion_face(self):
        return self._current_turn_emotion_face

    def clear_turn_emotion_face(self):
        self._current_turn_emotion_face = None

    async def _notify(self, *, text):
        if self._tools_closed or self._announce_result is None:
            return False
        result = self._announce_result(text)
        if inspect.isawaitable(result):
            result = await result
        return result is not False

    def _own_background(self, coroutine, *, name):
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def close(self):
        self._tools_closed = True
        current = asyncio.current_task()
        owner = self._execution_owner.get()
        executions = {
            task
            for task, task_owner in self._active_executions.items()
            if owner is None or task_owner is not owner
        }
        tasks = list(
            (self._background_tasks | self._emotion_tasks | executions) - {current}
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._mac is not None:
            for task_id in self._owned_mac_tasks:
                await self._mac.cancel_task(task_id)
        for extension in self._extensions:
            if extension.close is not None:
                await extension.close()

    @staticmethod
    def _tool_call_cancellation_ids(cancellation: Any) -> list[str]:
        raw_ids = (
            getattr(cancellation, "ids", None)
            or getattr(cancellation, "function_call_ids", None)
            or getattr(cancellation, "function_ids", None)
            or []
        )
        if isinstance(raw_ids, str):
            return [raw_ids]
        return [str(item) for item in raw_ids if item is not None]

    async def _handle_tool_call_cancellation(self, cancellation: Any) -> None:
        ids = self._tool_call_cancellation_ids(cancellation)
        for call_id in ids:
            self._cancelled_tool_call_ids.add(call_id)
            self._status.on_tool_call_cancelled()
            task_id = self._tool_call_task_ids.get(call_id)
            if task_id is None:
                logger.info(
                    "Gemini Live tool call cancelled before background task: %s",
                    call_id,
                )
                continue
            if self._mac is None:
                logger.info(
                    "Gemini Live tool call cancelled but Mac control is disabled: %s",
                    call_id,
                )
                continue
            result = await self._mac.cancel_task(task_id)
            logger.info(
                "Gemini Live tool call cancellation id=%s task_id=%s result=%s",
                call_id,
                task_id,
                result,
            )

    async def dispatch_calls(self, function_calls: list[Any]) -> list[dict[str, Any]]:

        responses: list[dict[str, Any] | None] = [None] * len(function_calls)

        async def dispatch_one(index: int, fc: Any) -> None:
            name = getattr(fc, "name", "")
            call_id = getattr(fc, "id", None)
            call_id_text = str(call_id) if call_id is not None else ""
            if call_id_text and call_id_text in self._cancelled_tool_call_ids:
                responses[index] = dict(
                    id=call_id,
                    name=name,
                    response={
                        "ok": False,
                        "cancelled": True,
                        "error": "tool call cancelled before start",
                    },
                )
                return
            args = dict(getattr(fc, "args", None) or {})
            result = await self._dispatch_tool_with_timeout(name, args)
            task_id = result.get("task_id")
            if call_id_text and isinstance(task_id, int):
                self._tool_call_task_ids[call_id_text] = task_id
            responses[index] = dict(
                id=call_id,
                name=name,
                response=result,
            )

        async def dispatch_serial(items: list[tuple[int, Any]]) -> None:
            for index, fc in items:
                await dispatch_one(index, fc)

        serial_groups: dict[str, list[tuple[int, Any]]] = {}
        parallel_tasks: list[asyncio.Task[None]] = []
        for index, fc in enumerate(function_calls):
            name = str(getattr(fc, "name", ""))
            meta = self._tool_metadata(name)
            if not meta.side_effect:
                parallel_tasks.append(asyncio.create_task(dispatch_one(index, fc)))
                continue
            group = meta.exclusive_group or f"tool:{name}"
            serial_groups.setdefault(group, []).append((index, fc))

        for items in serial_groups.values():
            parallel_tasks.append(asyncio.create_task(dispatch_serial(items)))

        if parallel_tasks:
            await asyncio.gather(*parallel_tasks)

        return [resp for resp in responses if resp is not None]

    def _tool_metadata(self, name: str) -> ToolMetadata:
        return self._extension_metadata.get(
            name, TOOL_METADATA.get(name, ToolMetadata(True, f"tool:{name}", 10.0))
        )

    async def _dispatch_tool_with_timeout(
        self,
        name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        meta = self._tool_metadata(name)
        try:
            return await asyncio.wait_for(
                self._dispatch_tool(name, args),
                timeout=meta.timeout_s,
            )
        except asyncio.TimeoutError:
            # Previously silent: the only trace was the bridge's outcome line.
            logger.warning("Gemini Live tool call name=%s timed out after %.0fs", name, meta.timeout_s)
            result = {"ok": False, "error": f"tool timeout after {meta.timeout_s:.0f}s"}
            if name == "show_character":
                result["note"] = "Display was slow; you may call show_character again once."
            return result
        except Exception as exc:
            logger.exception("Gemini Live tool %s dispatch crashed", name)
            return {"ok": False, "error": str(exc)}

    async def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self._dispatch_tool_impl(name, args)
        if isinstance(result.get("task_id"), int):
            self._owned_mac_tasks.add(result["task_id"])
        self._log_tool_result(name, args, result)
        return result

    async def _dispatch_tool_impl(
        self, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Map a Gemini function call to the right ESP32 MCP tool.

        Prefers the USB Serial/JTAG channel when wired up: same low-latency
        path TrackingBridge uses. Falls back to the WebSocket esp32.call_tool
        on any USB failure, and skips entirely when both channels are down.
        """
        if name in self._extension_routes:
            return await self._extension_routes[name].dispatch(name, args)
        from .xiaoya_forms import get_xiaoya_forms
        get_xiaoya_forms().note_interaction()
        if name == "end_homework_session":
            get_xiaoya_forms().end_homework_session()
            return {"ok": True, "spoken": "拜拜～聽日見！"}
        if name == "end_conversation":
            return await self._dispatch_end_conversation()
        if name == "set_all_leds" and self._should_defer_set_all_leds():
            logger.info(
                "Gemini Live set_all_leds deferred: wake_gate_state=%s args=%s",
                WakeGateState.LISTENING.value,
                self._summarize_tool_args(args),
            )
            return {"ok": True, "deferred": "listening_status_light_active"}
        if name == "express_emotion":
            return await self._dispatch_express_emotion(args)
        if name == "ask_grokbot":
            return await self._dispatch_ask_grokbot(args)
        if name == "ask_claude":
            return await self._dispatch_ask_claude(args)
        if name == "battery_status":
            return self._dispatch_battery_status()
        if name == "get_current_datetime":
            return self._dispatch_get_datetime()
        if name == "homework_vocab_context":
            from .xiaoya_forms import get_xiaoya_forms

            forms = get_xiaoya_forms()
            try:
                forms.set_vocab_context(args["item"], copy_mode_said=args["copy_mode_said"])
            except (ValueError, LookupError) as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "copy_mode_said": forms.copy_mode_said}
        if name == "show_character":
            return await self._dispatch_show_character(args)
        if name == "play_minigame":
            from .xiaoya_games import get_xiaoya_games
            from .xiaoya_forms import get_xiaoya_forms

            games = get_xiaoya_games()
            return games.consume_invite_start() if games.has_pending() else games.refuse_play(get_xiaoya_forms().snapshot())
        if name in {"homework_list", "homework_done", "homework_photo_snap", "homework_photo_cancel"}:
            if name == "homework_photo_snap":
                from .homework_photo import get_homework_photo
                return await get_homework_photo().attempt_capture_async()
            return self._dispatch_homework(name, args)
        if name in {"note_homework_stuck", "notify_dad_stuck"}:
            return self._dispatch_stuck(name, args)
        if name in {"self.tracking.start", "self.tracking.stop"}:
            if self._set_face_tracking is None:
                return {"ok": False, "error": "face tracking unavailable"}
            return await self._set_face_tracking(name == "self.tracking.start")
        if name in MAC_TOOL_NAMES:
            if self._mac is None:
                return {"ok": False, "error": "Mac control is turned off"}
            return await self._mac.dispatch(name, args)

        esp32_name = TOOL_MAP.get(name)
        if esp32_name is None:
            logger.warning("Gemini Live unknown tool name: %s", name)
            return {"ok": False, "error": f"unknown tool {name}"}

        normalized = self._normalize_args(name, args)
        usb_up = self._usb_transport is not None and getattr(
            self._usb_transport, "connected", False
        )
        ws_up = getattr(self._esp32, "device_connected", False)
        if not usb_up and not ws_up:
            return {"ok": False, "error": "device offline"}

        if usb_up:
            if name == "move_head" and self._on_head_command is not None:
                self._on_head_command()
            try:
                resp = await self._usb_transport.call_tool(esp32_name, normalized)
                return {"ok": True, "result": resp.get("result")}
            except Exception as exc:
                logger.warning(
                    "Gemini Live USB call %s failed (%s); falling back to WS",
                    esp32_name,
                    exc,
                )

        if not ws_up:
            return {"ok": False, "error": "device offline"}

        if not self._esp32_mcp_supported():
            if name == "move_head":
                return await self._dispatch_device_head(normalized)
            if name == "set_avatar":
                return await self._dispatch_device_emotion(normalized["face"])
            if name == "set_all_leds":
                return await self._dispatch_device_led(normalized)
            return {"ok": False, "error": "ESP32 MCP unsupported (features.mcp=false)"}

        try:
            result, error = await self._esp32.call_tool(esp32_name, normalized)
        except Exception as exc:
            logger.exception("Gemini Live tool %s dispatch failed", name)
            return {"ok": False, "error": str(exc)}

        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {"ok": True, "result": result}

    def _esp32_mcp_supported(self) -> bool:
        connection = getattr(self._esp32, "connection", None)
        if connection is None:
            return True
        return bool(getattr(connection, "mcp_supported", True))

    async def _dispatch_device_emotion(self, face: str) -> dict[str, Any]:
        emotion = face_to_device_emotion(face)
        send_emotion = getattr(self._esp32, "send_emotion", None)
        if not callable(send_emotion):
            return {"ok": False, "error": "device emotion channel unavailable"}
        try:
            _result, error = await send_emotion(emotion, notify_activity=False)
        except Exception as exc:
            logger.exception("Gemini Live emotion dispatch failed")
            return {"ok": False, "error": str(exc)}
        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {"ok": True, "emotion": emotion}

    async def _dispatch_device_head(self, pose: dict[str, int]) -> dict[str, Any]:
        send_head = getattr(self._esp32, "send_head", None)
        if not callable(send_head):
            return {"ok": False, "error": "device head channel unavailable"}
        if self._on_head_command is not None:
            self._on_head_command()
        try:
            _result, error = await send_head(
                pose["yaw"],
                pose["pitch"],
                pose["speed"],
                notify_activity=False,
                notify_head_command=self._on_head_command is None,
            )
        except Exception as exc:
            logger.exception("Gemini Live head dispatch failed")
            return {"ok": False, "error": str(exc)}
        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {
            "ok": True,
            "head": {
                "yaw": pose["yaw"],
                "pitch": pose["pitch"],
                "speed": pose["speed"],
            },
        }

    def _should_defer_set_all_leds(self) -> bool:
        # The speaking blue and the listening cyan are owned by the proxy; the model must not override them.
        if self._status.tts_active:
            return True
        getter = self._wake_gate_state_getter
        if getter is None:
            return False
        try:
            state = getter()
        except Exception:
            logger.debug("wake gate state getter failed; allowing set_all_leds")
            return False
        return state == WakeGateState.LISTENING.value

    async def _dispatch_device_led(self, rgb: dict[str, int]) -> dict[str, Any]:
        send_led = getattr(self._esp32, "send_led", None)
        if not callable(send_led):
            return {"ok": False, "error": "device led channel unavailable"}
        try:
            _result, error = await send_led(
                rgb["r"],
                rgb["g"],
                rgb["b"],
                notify_activity=False,
            )
        except Exception as exc:
            logger.exception("Gemini Live LED dispatch failed")
            return {"ok": False, "error": str(exc)}
        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {"ok": True, "led": {"r": rgb["r"], "g": rgb["g"], "b": rgb["b"]}}

    @staticmethod
    def _summarize_tool_args(args: dict[str, Any]) -> str:
        try:
            summary = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except TypeError:
            summary = repr(args)
        if len(summary) > 240:
            return summary[:237] + "..."
        return summary

    def _log_tool_result(
        self,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        ok = bool(result.get("ok"))
        if ok and result.get("deferred"):
            status = f"deferred={result['deferred']}"
        else:
            status = "ok" if ok else f"error={result.get('error', 'unknown')}"
        logger.info(
            "Gemini Live tool call name=%s args=%s %s",
            name,
            self._summarize_tool_args(args),
            status,
        )
        self._status.record_tool_call(
            name,
            ok,
            error=None if ok else str(result.get("error", "unknown")),
        )

    async def _dispatch_express_emotion(self, args: dict[str, Any]) -> dict[str, Any]:
        from .emotion_map import resolve as resolve_emotion  # noqa: PLC0415

        mood = str(args.get("mood") or "neutral")
        intensity = str(args.get("intensity") or "low")
        plan = resolve_emotion(mood, intensity)
        self._current_turn_emotion_face = plan.face

        task = asyncio.create_task(
            self._dispatch_emotion_plan(plan),
            name="gemini-live-express-emotion",
        )
        self._emotion_tasks.add(task)
        task.add_done_callback(self._emotion_tasks.discard)

        r, g, b = plan.led_rgb
        return {
            "ok": True,
            "face": plan.face,
            "led": {"r": r, "g": g, "b": b},
            "head": {"yaw": plan.head_yaw, "pitch": plan.head_pitch},
        }

    async def _set_face(self, face: str) -> None:
        """Best-effort avatar change; visual feedback, never blocks a turn."""
        try:
            await self._dispatch_tool("set_avatar", {"face": face})
        except Exception:
            logger.debug("set_face(%s) failed", face, exc_info=True)

    async def _dispatch_ask_grokbot(self, args: dict[str, Any]) -> dict[str, Any]:
        """Hand a task to the Grok Bot agent and return at once.

        The agent runs in the Grok Bot app and has no way to reach the
        robot's speaker, so every reply piece that comes back through the
        forwarding service (gbot_http_proxy, default 127.0.0.1:18770) is
        pushed into the Live session as a system notice, and Gemini says it
        in its own voice (same approach as ``_announce_mac_task``). If
        nothing comes back, a one-line failure notice is pushed instead.
        """
        from . import gbot_brain

        bot = tool_bot()
        if not bot:
            return {
                "ok": False,
                "error": "ask_grokbot is off: STACKCHAN_TOOL_BOT is not set",
            }
        task = str(args.get("task") or args.get("question") or "").strip()
        if not task:
            return {"ok": False, "error": "task is required"}
        face_task = asyncio.create_task(self._set_face("thinking"))
        face_task.add_done_callback(lambda _t: None)
        bot_id = _bot_setting("tool_bot_id", "STACKCHAN_TOOL_BOT_ID")
        timeout_s = float(_bot_setting("ask_timeout_s", "STACKCHAN_ASK_TIMEOUT", "110"))
        prefix = _bot_setting("task_prefix", "STACKCHAN_TOOL_BOT_PREFIX")
        message = f"{prefix}{task}" if prefix else task

        async def _run_and_announce() -> None:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

            def _pump() -> None:
                try:
                    for event in gbot_brain.iter_gbot_replies(
                        message, timeout_s=timeout_s, bot=bot, bot_id=bot_id
                    ):
                        loop.call_soon_threadsafe(queue.put_nowait, ("reply", event))
                except Exception as exc:  # noqa: BLE001
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, ("end", None))

            pump = asyncio.create_task(asyncio.to_thread(_pump))
            pump.add_done_callback(lambda _t: None)
            spoken = 0
            error: Exception | None = None
            while True:
                kind, item = await queue.get()
                if kind == "end":
                    break
                if kind == "error":
                    error = item
                    continue
                piece = str((item or {}).get("reply") or "").strip()
                if not piece:
                    continue
                spoken += 1
                logger.info(
                    "ask_grokbot reply #%s event=%s chars=%s",
                    spoken,
                    (item or {}).get("event"),
                    len(piece),
                )
                announce = self._announce_result
                if announce is None:
                    logger.info("ask_grokbot reply arrived but no live session")
                    continue
                try:
                    await self._notify(
                        text=f"[System notice, not the user speaking] {bot} replied: "
                        f"{piece}\nPass this on to the user briefly, right away, in your own voice and "
                        "in the user's language. Do not call any tools, and do not call ask_grokbot again."
                    )
                except Exception:
                    logger.exception("ask_grokbot reply announcement failed")
            if spoken:
                return
            logger.warning("ask_grokbot background failed: %s", error or "no reply")
            announce = self._announce_result
            if announce is None:
                return
            try:
                await self._notify(
                    text=f"[System notice, not the user speaking] Sending the task to {bot} failed. "
                    f"Tell the user in one sentence, in their language, that it did not get through. "
                    f"Do not make up a reply from {bot}."
                )
            except Exception:
                logger.exception("ask_grokbot failure announcement failed")

        self._own_background(_run_and_announce(), name="ask_grokbot")
        return {
            "ok": True,
            "status": "sent",
            "say": f"Sent to {bot}.",
            "instruction": f"Right away, tell the user in your own voice and in their language that the "
            f"task has been sent to {bot}. Do not wait for the reply; a system notice follows when "
            f"{bot} answers.",
        }

    async def _dispatch_ask_claude(self, args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question", "")).strip()
        if not question:
            return {"ok": False, "error": "question is required"}
        # Thinking face while Claude works; the reply's express_emotion (or
        # the TTS mouth animation) takes the screen back afterwards.
        face_task = asyncio.create_task(self._set_face("thinking"))
        face_task.add_done_callback(lambda _t: None)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._claude_bin,
                "-p",
                question,
                *claude_fast_args(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=30.0,
            )
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        except FileNotFoundError:
            return {"ok": False, "error": f"{self._claude_bin} not found on PATH"}
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": "claude timed out (30s)"}
        except Exception as exc:
            logger.exception("ask_claude subprocess failed")
            return {"ok": False, "error": str(exc)}
        if proc.returncode != 0:
            return {"ok": False, "error": stderr.decode(errors="replace").strip()}
        return {"ok": True, "answer": stdout.decode(errors="replace").strip()}

    @staticmethod
    async def _dispatch_show_character(args: dict[str, Any]) -> dict[str, Any]:
        from .char_structure import lookup_char_structure
        from .xiaoya_forms import get_xiaoya_forms
        from .xiaoya_glyph import (
            DEFAULT_HOLD_MS,
            clean_characters,
            glyph_message,
            render_glyph_png,
            vocab_mode,
        )

        try:
            c = clean_characters(args.get("character"))
        except ValueError:
            return {"ok": False, "error": "bad_character", "spoken": "我聽唔清係邊個字，你講埋個詞語俾我聽吖。"}
        forms = get_xiaoya_forms()
        try:
            items = forms.snapshot()["items"]
        except Exception:  # undeterminable -> treat as dictation (spec section 10)
            items = None
        if vocab_mode(items, copy_mode_said=forms.copy_mode_said) == "dictation":
            # 默書 (or mode unknown): never render or send the character.
            # Only mention 默書 when an unfinished 默 item really exists; an empty or
            # unreadable list is still treated as dictation, but said honestly.
            has_dictation = isinstance(items, list) and any(
                isinstance(item, dict)
                and isinstance(item.get("title"), str)
                and "默" in item["title"]
                and not item.get("done")
                for item in items
            )
            # Hint only the first glyph; multi-char requests stay encourage-only
            # unless we later extend the tool to per-char hints.
            hint_char = c[0]
            hints = lookup_char_structure(hint_char)
            if hints:
                spoken = (
                    "今日要默書，我唔出個字。我講吓提示，你自己諗吓。"
                    if has_dictation
                    else "我唔出個字住。我講吓提示俾你諗吓。"
                )
                note = (
                    "Dictation: never show, spell or go stroke-by-stroke through "
                    "the full character, and do not list every component. In 背默 "
                    "never say the character or the text; in 讀默 you may re-read "
                    "the dictated sentence, nothing more. Speak ONLY the radical/"
                    "structure fields provided in this result (HK Cantonese nicknames); "
                    "never invent 部首 or 結構. "
                    + SHOW_CHARACTER_FRESH_NOTE
                )
            else:
                spoken = (
                    "今日要默書，我唔出個字。你自己諗吓。"
                    if has_dictation
                    else "我唔出個字住。你自己諗吓。"
                )
                note = (
                    "Dictation: never show, spell or go stroke-by-stroke through "
                    "the full character, and do not list every component. In 背默 "
                    "never say the character or the text; in 讀默 you may re-read "
                    "the dictated sentence, nothing more. No radical/structure was "
                    "provided—only encourage (「你自己諗吓」); never invent 部首 or 結構. "
                    + SHOW_CHARACTER_FRESH_NOTE
                )
            result = {
                "ok": True,
                "shown": False,
                "mode": "dictation",
                "character": c,
                "spoken": spoken,
                "note": note,
            }
            result.update(hints)
            return result
        try:
            png = await asyncio.to_thread(render_glyph_png, c)
            sent = await forms.send_device(glyph_message(png))
        except ValueError:
            sent = False
        if not sent:
            return {"ok": False, "shown": False, "mode": "copy",
                    "spoken": "我而家顯示唔到個字，你可以問吓爸爸媽媽。",
                    "note": (
                        "Copy mode but display failed. Do not invent 部首/結構. "
                        + SHOW_CHARACTER_FRESH_NOTE
                    )}
        return {
            "ok": True,
            "shown": True,
            "mode": "copy",
            "character": c,
            "seconds": DEFAULT_HOLD_MS // 1000,
            "spoken": "睇清楚個字，收起咗之後自己喺簿度寫一次。",
            "note": (
                "Copy/shown: the glyph is on screen. Follow spoken only—"
                "do NOT add 部首 or 結構 commentary. "
                + SHOW_CHARACTER_FRESH_NOTE
            ),
        }

    @staticmethod
    def _dispatch_homework(name: str, args: dict[str, Any]) -> dict[str, Any]:
        from .xiaoya_forms import get_xiaoya_forms  # noqa: PLC0415

        from .camera_presence import get_camera_presence
        presence = get_camera_presence()
        forms = get_xiaoya_forms()
        if name == "homework_list":
            snap = forms.snapshot()
            presence.start_homework_session()
            forms.start_homework_session()
            if not snap["items"]:
                return {"ok": True, "items": [], "spoken": "今日清單未有功課呀，叫爸爸加啦。",
                        "note": HOMEWORK_LIST_FRESH_NOTE}
            return {
                "ok": True,
                "items": [
                    {"id": item["id"], "title": item["title"], "done": item["done"]}
                    for item in snap["items"]
                ],
                "done": snap["done"],
                "total": snap["total"],
            }
        from .homework_photo import get_homework_photo
        photo = get_homework_photo()
        if name == "homework_photo_snap":
            return photo.attempt_capture()
        if name == "homework_photo_cancel":
            return photo.cancel()
        return photo.resolve(args.get("item"))

    @staticmethod
    def _dispatch_stuck(name: str, args: dict[str, Any]) -> dict[str, Any]:
        from .stuck_notify import get_stuck_notify  # noqa: PLC0415

        stuck = get_stuck_notify()
        item = args.get("item")
        try:
            if name == "note_homework_stuck":
                return stuck.note_stuck(
                    item,
                    args.get("point") or "",
                    give_up=bool(args.get("give_up")),
                )
            return stuck.notify_dad(
                item,
                args.get("stuck_point") or "",
                consented=bool(args.get("consented")),
            )
        except ValueError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "spoken": "你再講一次係邊樣功課卡住？",
            }

    @staticmethod
    def _dispatch_get_datetime() -> dict[str, Any]:
        from datetime import datetime  # noqa: PLC0415

        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Hong_Kong"))

        def zh(number):
            digits = "零一二三四五六七八九"
            if number < 10:
                return digits[number]
            tens, ones = divmod(number, 10)
            return (digits[tens] if tens > 1 else "") + "十" + (digits[ones] if ones else "")

        hour = now.hour
        if hour < 6:
            period = "凌晨"
        elif hour < 12:
            period = "朝早"
        elif hour == 12:
            period = "中午"
        elif hour < 18:
            period = "下晝"
        else:
            period = "夜晚"
        minutes = (("零" if now.minute < 10 else "") + zh(now.minute) + "分") if now.minute else "正"
        spoken = (
            f"而家係香港時間{zh(now.month)}月{zh(now.day)}號"
            f"星期{'一二三四五六日'[now.weekday()]}，"
            f"{period}{zh(hour % 12 or 12)}點{minutes}。"
        )
        if _language_mode() == "cmn":
            # 46c: Mandarin wording while the session is in Mandarin mode.
            cmn_period = (
                "凌晨" if hour < 6 else "上午" if hour < 12 else "中午" if hour == 12
                else "下午" if hour < 18 else "晚上"
            )
            h12 = hour % 12 or 12
            cmn_hour = "兩" if h12 == 2 else zh(h12)
            cmn_minutes = (
                (("零" if now.minute < 10 else "") + zh(now.minute) + "分")
                if now.minute
                else "整"
            )
            spoken = (
                f"現在是香港時間{zh(now.month)}月{zh(now.day)}日"
                f"星期{'一二三四五六日'[now.weekday()]}，"
                f"{cmn_period}{cmn_hour}點{cmn_minutes}。"
            )
        weekdays = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        return {
            "ok": True,
            "datetime": now.isoformat(timespec="seconds"),
            "timezone": "Asia/Hong_Kong",
            "spoken_zh": spoken,
            "weekday": weekdays[now.weekday()],
            "readable": now.strftime("%Y-%m-%d %H:%M") + " " + weekdays[now.weekday()],
        }

    @staticmethod
    def _dispatch_battery_status() -> dict[str, Any]:
        status = get_debug_status()
        percent = status.device_battery_percent
        at = status.device_battery_at
        stale = (
            not status.device_connected_flag
            or percent is None
            or at is None
            or status.clock() - at > 600
        )
        reason = getattr(status, "device_battery_unknown_reason", None)
        cmn = _language_mode() == "cmn"
        # 46d: only claim "plugged in" when the firmware says so (usb_pinned);
        # with no report the robot just says it cannot read the level.
        if stale and reason == "usb_pinned":
            spoken = (
                "我現在插著電在用，所以讀不到電池有多少電，不用擔心喔。"
                if cmn
                else "我而家插住電用緊，所以讀唔到電池有幾多電，唔使擔心呀。"
            )
        elif stale:
            spoken = (
                "我現在讀不到自己有多少電。想知道的話，可以問問爸爸媽媽。"
                if cmn
                else "我而家讀唔到自己有幾多電。想知就問吓爸爸媽媽睇吓啦。"
            )
        else:
            spoken = f"我還有大約{percent}%的電。" if cmn else f"我仲有大約{percent}%電。"
            if percent <= 20:
                spoken += (
                    " 有點沒電了，記得叫爸爸媽媽幫我充電喔。"
                    if cmn
                    else " 有啲少電喇，記得叫爸爸媽媽幫我充電呀。"
                )
        return {"ok": True, "percent": percent, "stale": stale, "reason": reason, "spoken": spoken}

    async def _announce_mac_task(self, record: dict[str, Any]) -> None:
        """Push a background-task result into the live session so the robot
        speaks up on its own. If the session is down the result stays
        queryable via check_mac_task."""
        if self._tools_closed:
            return
        announce = self._announce_result
        if announce is None:
            logger.info(
                "mac task #%s finished (%s) but no live session; "
                "result held for check_mac_task",
                record.get("id"),
                record.get("status"),
            )
            await self._set_face("notification")
            return
        status = "finished" if record.get("status") == "done" else "failed"
        result = str(record.get("result") or "").strip()
        text = (
            f"[System notice, not the user speaking] Background task #{record.get('id')}"
            f" ({record.get('task')}) has {status}. Result: {result or 'no output'}. "
            "Report it to the user in one or two sentences, in their language."
        )
        try:
            if not await self._notify(text=text):
                await self._set_face("notification")
        except Exception:
            logger.exception("mac task announcement failed")

    async def _dispatch_emotion_plan(self, plan: Any) -> None:
        r, g, b = plan.led_rgb
        results = await asyncio.gather(
            self._dispatch_tool("set_avatar", {"face": plan.face}),
            self._dispatch_tool("set_all_leds", {"r": r, "g": g, "b": b}),
            self._dispatch_tool(
                "move_head",
                {"yaw": plan.head_yaw, "pitch": plan.head_pitch, "speed": 450},
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Gemini Live emotion dispatch failed: %s", result)

    async def _dispatch_end_conversation(self) -> dict[str, Any]:
        callback = getattr(self._esp32, "on_end_conversation", None)
        if callback is None and self._on_end_conversation is None:
            logger.warning(
                "Gemini Live end_conversation requested but no handler is wired"
            )
            return {"ok": False, "error": "end_conversation unavailable"}
        if self._cancel_idle is not None:
            self._cancel_idle()
        result = None
        handler_error: Exception | None = None
        try:
            if callback is not None:
                result = callback()
                if inspect.isawaitable(result):
                    result = await result
        except Exception as exc:
            handler_error = exc
            logger.exception("Gemini Live end_conversation handler failed")
        try:
            if self._on_end_conversation is not None:
                extra = self._on_end_conversation()
                if inspect.isawaitable(extra):
                    await extra
        except Exception as exc:
            logger.exception("Gemini Live end_conversation callback failed")
            if handler_error is None:
                handler_error = exc
        if handler_error is not None:
            return {"ok": False, "error": str(handler_error)}
        return {"ok": True, "result": result}

    @staticmethod
    def _normalize_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Clamp / coerce Gemini-provided args before they hit the firmware."""
        if name == "move_head":
            yaw = max(-90, min(90, int(args.get("yaw", 0))))
            pitch = max(0, min(60, int(args.get("pitch", 0))))
            # Firmware: Property("speed", ..., MOTION_DEFAULT_SPEED, 100, 1000)
            # and the AddTool description says "150 natural"; that's the
            # ergonomic default, not 300.
            speed = max(100, min(1000, int(args.get("speed", 150))))
            return {"yaw": yaw, "pitch": pitch, "speed": speed}
        if name == "set_avatar":
            face = str(args.get("face", "idle"))
            return {"face": face}
        if name == "set_all_leds":
            return {
                "r": max(0, min(255, int(args.get("r", 0)))),
                "g": max(0, min(255, int(args.get("g", 0)))),
                "b": max(0, min(255, int(args.get("b", 0)))),
            }
        return args
