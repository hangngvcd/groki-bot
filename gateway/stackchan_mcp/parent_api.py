# SPDX-License-Identifier: MIT
"""Opt-in, loopback-only parent API, separate from the device HTTP server."""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
from pathlib import Path
import re
import secrets

from aiohttp import ClientSession, ClientTimeout, web

from .debug_status import get_debug_status
from .parent_settings import (
    SCHEMA,
    device_support,
    get_parent_settings,
    private_directory,
)

logger = logging.getLogger(__name__)


def parent_api_enabled(env=None):
    return (os.environ if env is None else env).get("STACKCHAN_PARENT_API") == "1"


def resolve_parent_token():
    token = os.getenv("STACKCHAN_PARENT_TOKEN")
    if token:
        return token
    path = Path(
        os.getenv("STACKCHAN_PARENT_TOKEN_FILE", "~/.stackchan/parent_token")
    ).expanduser()
    try:
        token = path.read_text().strip()
        if not token:
            raise ValueError("Empty parent token file")
        path.chmod(0o600)
        return token
    except FileNotFoundError:
        private_directory(path.parent)
        token = secrets.token_urlsafe(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return resolve_parent_token()
        with os.fdopen(fd, "w") as stream:
            stream.write(token + "\n")
        logger.info("Parent token generated at %s", path)
        return token


def build_parent_status(debug_status_snapshot, settings, env, helper_health):
    snap = debug_status_snapshot
    device = snap.get("device", {})
    voice = snap.get("gemini", {})
    wake = snap.get("wake_gate", {})
    error = voice.get("last_error") or {}
    result = {
        "device": {
            key: device.get(key)
            for key in (
                "connected",
                "connected_since",
                "last_disconnect_at",
                "battery_percent",
            )
        },
        "voice": {
            "backend": env.get("STACKCHAN_VOICE_BACKEND", "gemini"),
            "session_connected": bool(voice.get("connected")),
            "last_error": error.get("message"),
            "login_helper": {
                key: bool(helper_health.get(key))
                for key in ("reachable", "agent_configured", "desktop_enabled")
            },
        },
        "wake": {
            "phrase": (env.get("WAKE_PHRASE") or env.get("STACKCHAN_WAKE_PHRASE") or "hi grok").strip(),
            "wake_word_enabled": env.get("STACKCHAN_WAKE_WORD", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            **{
                key: wake.get(key)
                for key in ("available", "state", "listening_mode", "last_wake_at")
            },
        },
        "child_profile": env.get("STACKCHAN_CHILD_PROFILE", "0").strip().lower()
        in {"1", "true", "yes", "on"},
        "tts_active": bool(snap.get("audio", {}).get("tts_active")),
        "settings": dict(settings),
        "device_support": device_support(),
    }
    battery = device.get("battery_percent")
    battery_text = "未有資料" if battery is None else f"{battery}%"
    connected = "已連線" if device.get("connected") else "未連線"
    mode = wake.get("listening_mode")
    if mode == "wake_word":
        mode_text = f"叫「{result['wake']['phrase']}」或者撳掣"
    elif mode == "button_only":
        mode_text = "只准撳掣"
    elif mode == "pass_through":
        mode_text = "直接轉發（冇 wake word）"
    else:
        mode_text = "未知"
    voice_text = "已連線" if result["voice"]["session_connected"] else "未連線"
    result["summary_zh"] = (
        f"小芽{connected}；語音{voice_text}；聆聽模式：{mode_text}；"
        f"電量：{battery_text}；音量 {settings['volume']}；"
        f"休息 {settings['rest_after_min']} 分鐘"
    )
    return result


async def helper_health():
    """Cheap check of the Grok login helper (gbot backend only).

    Only ``GET /health`` is used: ``/native/health`` performs a full desktop
    handshake and is too slow and heavy for a status question.
    """
    if os.getenv("STACKCHAN_VOICE_BACKEND", "gemini").strip().lower() != "gbot":
        return {}
    base = os.getenv("STACKCHAN_GBOT_VOICE_URL", "http://127.0.0.1:18771").rstrip("/")
    try:
        async with ClientSession(timeout=ClientTimeout(total=2), trust_env=False) as session:
            async with session.get(base + "/health") as response:
                response.raise_for_status()
                health = await response.json()
        return {
            "reachable": True,
            "agent_configured": bool(health.get("agentId")),
            "desktop_enabled": bool(health.get("desktopEnabled")),
        }
    except Exception:
        return {"reachable": False, "agent_configured": False, "desktop_enabled": False}


def create_parent_app(token, settings=None, debug_status=None):
    device_token = os.getenv("STACKCHAN_TOKEN", "")
    if device_token and hmac.compare_digest(token.encode(), device_token.encode()):
        logger.error("Parent API refused: parent token equals device token")
        raise ValueError("Parent token must differ from device token")
    settings = settings if settings is not None else get_parent_settings()
    debug_status = debug_status if debug_status is not None else get_debug_status()

    @web.middleware
    async def guard(request, handler):
        try:
            loopback = ipaddress.ip_address(request.remote or "").is_loopback
        except ValueError:
            loopback = False
        host = request.headers.get("Host", "")
        if (
            not loopback
            or not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]+)?", host, re.I)
            or "Origin" in request.headers
        ):
            return web.json_response({"ok": False, "error": "Forbidden"}, status=403)
        bearer = request.headers.get("Authorization", "")
        supplied = (
            bearer.removeprefix("Bearer ") if bearer.startswith("Bearer ") else ""
        )
        if (
            not supplied
            or (
                device_token
                and hmac.compare_digest(supplied.encode(), device_token.encode())
            )
            or not hmac.compare_digest(supplied.encode(), token.encode())
        ):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
        return await handler(request)

    async def status(request):
        payload = build_parent_status(
            debug_status.snapshot(), settings.get(), os.environ, await helper_health()
        )
        return web.json_response({"ok": True, "status": payload})

    async def get_settings(request):
        return web.json_response(
            {
                "ok": True,
                "settings": settings.get(),
                "schema": SCHEMA,
                "device_support": device_support(),
            }
        )

    async def post_settings(request):
        try:
            result = settings.update(await request.json(), source="parent-api")
        except (ValueError, TypeError) as exc:
            # Validation messages name only the key and the allowed range.
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        return web.json_response(
            {"ok": True, **result, "device_support": device_support()}
        )

    app = web.Application(middlewares=[guard])
    app.router.add_get("/parent/v1/status", status)
    app.router.add_get("/parent/v1/settings", get_settings)
    app.router.add_post("/parent/v1/settings", post_settings)
    return app


async def start_parent_api():
    """Start the loopback parent API; any failure leaves the gateway running without it."""
    if not parent_api_enabled():
        return None
    try:
        app = create_parent_app(resolve_parent_token())
    except (OSError, ValueError) as exc:
        logger.error("Parent API not started: %s", exc)
        return None
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    port = int(os.getenv("STACKCHAN_PARENT_API_PORT", "18772"))
    try:
        await web.TCPSite(runner, "127.0.0.1", port).start()
    except OSError as exc:
        await runner.cleanup()
        logger.error("Parent API not started on 127.0.0.1:%d: %s", port, exc)
        return None
    except BaseException:
        await runner.cleanup()
        raise
    logger.info("Parent API listening on 127.0.0.1:%d (token file auth)", port)
    return runner
