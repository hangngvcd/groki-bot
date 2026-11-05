# SPDX-License-Identifier: MIT
"""Opt-in, loopback-only parent API, separate from the device HTTP server."""

from __future__ import annotations

import hmac
from datetime import date
import ipaddress
import logging
import os
from pathlib import Path
import re
import secrets

from aiohttp import ClientSession, ClientTimeout, web

from .debug_status import get_debug_status
from .xiaoya_forms import get_xiaoya_forms
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
    reason = device.get("battery_unknown_reason")
    if battery is not None:
        battery_text = f"{battery}%"
    elif reason == "usb_pinned":
        battery_text = "讀唔到（似係插住 USB 電，冇讀到電池）"
    elif reason:
        battery_text = "讀唔到（電池讀數異常）"
    else:
        battery_text = "未有資料"
    connected = "已連線" if device.get("connected") else "未連線"
    mode = wake.get("listening_mode")
    if mode in {"wake_word", "wake_word_or_button"}:
        mode_text = f"叫「{result['wake']['phrase']}」或者撳掣"
    elif mode == "button_only":
        mode_text = "只准撳掣"
    elif mode == "pass_through":
        mode_text = "直接轉發（冇 wake word）"
    else:
        mode_text = "未知"
    voice_text = "已連線" if result["voice"]["session_connected"] else "未連線"
    result["summary_zh"] = (
        f"鈴鈴{connected}；語音{voice_text}；聆聽模式：{mode_text}；"
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


def create_parent_app(token, settings=None, debug_status=None, forms=None, usage=None, papers=None):
    device_token = os.getenv("STACKCHAN_TOKEN", "")
    if device_token and hmac.compare_digest(token.encode(), device_token.encode()):
        logger.error("Parent API refused: parent token equals device token")
        raise ValueError("Parent token must differ from device token")
    settings = settings if settings is not None else get_parent_settings()
    debug_status = debug_status if debug_status is not None else get_debug_status()
    forms = forms if forms is not None else get_xiaoya_forms()
    settings.register_pusher("volume", forms.send_volume)
    from .battery_calibration import register_pushers, get_battery_calibration, fit_csv, validate_curve
    register_pushers(settings, forms)

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

    async def get_usage(request):
        from .usage_stats import get_usage_stats

        store = usage if usage is not None else get_usage_stats()
        try:
            day = request.query.get("date")
            result = store.snapshot(day)
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        day = day or store.now().date().isoformat()
        payload = {"ok": True, "date": day, "usage": result}
        week = weekly_section(day, request.query.get("weekly") in {"1", "true"})
        if week:
            payload["weekly_weak_points"] = week
        return web.json_response(payload)

    from .paper_analysis import Practice, format_weekly, get_paper_store, weekly_due

    def paper_store():
        return papers if papers is not None else get_paper_store()

    def weekly_section(day, force):
        store = paper_store()
        try:
            when = date.fromisoformat(day)
            if not (force or weekly_due(when)):
                return None
            week = store.weekly(when)
        except (OSError, ValueError):
            logger.warning("Weekly weak-point section unavailable", exc_info=True)
            return None
        text = format_weekly(week)
        return {**week, "text": text} if text else None

    def paper_error(exc):
        if isinstance(exc, LookupError):
            return web.json_response({"ok": False, "error": str(exc).strip("'\"")}, status=404)
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    async def paper_body(request):
        try:
            body = await request.json()
        except ValueError:
            raise ValueError("expected a JSON object") from None
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        return body

    async def get_paper_inbox(request):
        store = paper_store()
        try:
            store.purge()
            return web.json_response({"ok": True, "inbox": store.list_inbox(), "inbox_dir": str(store.inbox)})
        except OSError as exc:
            return paper_error(ValueError(f"paper storage unavailable: {exc.strerror}"))

    async def get_paper_reports(request):
        store = paper_store()
        try:
            if request.query.get("id"):
                return web.json_response({"ok": True, "id": request.query["id"], "markdown": store.read_report(request.query["id"])})
            store.purge()
            return web.json_response({"ok": True, "reports": store.list_reports()})
        except (LookupError, OSError) as exc:
            return paper_error(exc if isinstance(exc, LookupError) else ValueError("paper storage unavailable"))

    async def post_paper_report(request):
        store = paper_store()
        try:
            store.purge()
            result = store.submit_report(await paper_body(request))
        except (LookupError, ValueError, TypeError) as exc:
            return paper_error(exc)
        return web.json_response({"ok": True, **result, "next": "dad must confirm pending weak points"})

    async def get_weak_points(request):
        return web.json_response({"ok": True, "weak_points": paper_store().items()})

    async def post_weak_points(request):
        store = paper_store()
        try:
            body = await paper_body(request)
            if set(body) != {"action", "ids"}:
                raise ValueError('Expected {"action": confirm|pause|resume|delete, "ids": [...]}')
            changed = store.update(body["action"], body["ids"])
        except (LookupError, ValueError, TypeError) as exc:
            return paper_error(exc)
        return web.json_response({"ok": True, "changed": changed, "weak_points": store.items()})

    async def post_paper_purge(request):
        return web.json_response({"ok": True, "removed": paper_store().purge()})

    async def post_paper_delete_all(request):
        try:
            body = await paper_body(request)
            if set(body) - {"confirm", "expect"} or not isinstance(body.get("confirm", False), bool):
                raise ValueError('Expected {"confirm": false} to list, then {"confirm": true, "expect": N}')
            result = paper_store().delete_all(body.get("confirm", False), body.get("expect"))
        except (ValueError, TypeError) as exc:
            return paper_error(exc)
        return web.json_response({"ok": True, **result})

    async def post_paper_revision(request):
        store = paper_store()
        try:
            body = await paper_body(request)
            if set(body) - {"subject", "confirm", "titles"}:
                raise ValueError('Expected {"subject": ...} or {"confirm": true, "titles": [...]}')
            if not body.get("confirm"):
                return web.json_response({"ok": True, "proposal": store.revision(body.get("subject")),
                                          "next": "dad confirms before items join the homework list"})
            titles = body.get("titles")
            if not isinstance(titles, list) or not titles or not all(isinstance(t, str) and t.strip() for t in titles):
                raise ValueError("titles: expected the dad-confirmed list")
            snapshot = forms.add_items([t if t.startswith("溫書") else "溫書：" + t for t in titles], source="parent-revision")
        except (LookupError, ValueError, TypeError) as exc:
            return paper_error(exc)
        return web.json_response({"ok": True, "forms": snapshot})

    async def post_practice_extra(request):
        try:
            body = await paper_body(request)
            if set(body) != {"count"}:
                raise ValueError('Expected {"count": 1..5}')
            result = Practice(paper_store()).add_extra(body["count"])
        except (ValueError, TypeError) as exc:
            return paper_error(exc)
        return web.json_response({"ok": True, **result})

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

    async def get_homework(request):
        return web.json_response({"ok": True, "forms": forms.snapshot()})

    async def post_homework(request):
        # The parent assistant only edits the list (dad adds / removes / asks
        # for a redo).  Progress itself comes from 娜娜 marking items done.
        try:
            body = await request.json()
            if not isinstance(body, dict) or len(set(body) - {"reason"}) != 1 or ("reason" in body and "redo" not in body):
                raise ValueError('Expected one of {"add": [...]}, {"remove": [...]}, {"redo": [...]}')
            (op, value), = [(k, v) for k, v in body.items() if k != "reason"]
            if op == "add":
                snapshot = forms.add_items(value, source="parent-api")
            elif op == "remove":
                snapshot = forms.remove_items(value, source="parent-api")
            elif op == "redo":
                snapshot = forms.redo_items(value, source="parent-api", reason=body.get("reason", ""))
            else:
                raise ValueError("op: expected add / remove / redo")
        except LookupError as exc:
            return web.json_response({"ok": False, "error": str(exc).strip("'\"")}, status=404)
        except (ValueError, TypeError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        return web.json_response({"ok": True, "forms": snapshot})

    async def post_intro(request):
        if not await forms.replay_intro():
            return web.json_response(
                {"ok": False, "error": "鈴鈴未連線 / device not connected"}, status=409
            )
        return web.json_response({"ok": True})

    def volume_payload():
        report = forms.snapshot().get("device", {})
        return {
            "ok": True,
            "volume": settings.get()["volume"],
            "device_connected": forms.session_ready,
            "device_volume": report.get("volume"),
            "last_reset_reason": report.get("reset_reason"),
            "last_panic": report.get("panic"),
            "last_boot_report_at": report.get("at"),
        }

    async def get_volume(request):
        return web.json_response(volume_payload())

    async def post_volume(request):
        # {"volume": 0..100}; stored as the parent "volume" setting and sent to
        # 鈴鈴 now (if connected) and again on every reconnect.
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"volume"}:
                raise ValueError('Expected {"volume": 0..100}')
            result = settings.update({"volume": body["volume"]}, source="parent-api")
        except (ValueError, TypeError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        return web.json_response(
            {
                **volume_payload(),
                "changed": result["changed"],
                "applies": "now" if forms.session_ready else "next_connect",
            }
        )

    async def battery_cal(request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("expected calibration command object")
            action = body.get("action")
            cal = get_battery_calibration()
            if action == "start":
                result = {"csv": await cal.start(forms.send_device, body.get("interval_s", 30))}
            elif action == "stop":
                result = {"csv": await cal.stop(forms.send_device)}
            elif action == "fit":
                result = {"proposed_curve_raw": fit_csv(cal.csv_path(body.get("csv")))}
            elif action in {"apply", "reset"}:
                if action == "apply" and ("raw" in body) == bool(body.get("csv")):
                    raise ValueError("apply requires either csv or raw")
                raw = None if action == "reset" else (validate_curve(body["raw"]) if "raw" in body else fit_csv(cal.csv_path(body.get("csv"))))
                result = settings.update({"battery_curve_raw": raw}, source="parent-api")
            else:
                raise ValueError("action: expected start/stop/fit/apply/reset")
            return web.json_response({"ok": True, **result})
        except (OSError, ValueError, TypeError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    from .parent_recovery import RecoveryStore
    recovery = RecoveryStore()

    async def photos(request):
        return web.json_response({"ok": True, "photos": recovery.photos()})

    async def photo(request):
        try:
            result = recovery.photo(request.match_info["id"], done=request.method == "POST")
            return web.json_response({"ok": True, "photo": result})
        except LookupError:
            return web.json_response({"ok": False, "error": "photo not found"}, status=404)

    async def events(request):
        try:
            after = int(request.query.get("after", "0"))
            if after < 0:
                raise ValueError()
        except ValueError:
            return web.json_response({"ok": False, "error": "invalid after"}, status=400)
        return web.json_response({"ok": True, "events": recovery.events(after)})

    app = web.Application(middlewares=[guard])
    app.router.add_get("/parent/v1/photos", photos)
    app.router.add_get("/parent/v1/photos/{id}/path", photo)
    app.router.add_post("/parent/v1/photos/{id}/done", photo)
    app.router.add_get("/parent/v1/events", events)
    app.router.add_post("/parent/v1/battery-cal", battery_cal)
    app.router.add_get("/parent/v1/usage", get_usage)
    app.router.add_get("/parent/v1/status", status)
    app.router.add_get("/parent/v1/settings", get_settings)
    app.router.add_post("/parent/v1/settings", post_settings)
    app.router.add_get("/parent/v1/homework", get_homework)
    app.router.add_post("/parent/v1/homework", post_homework)
    app.router.add_post("/parent/v1/intro", post_intro)
    app.router.add_get("/parent/v1/volume", get_volume)
    app.router.add_post("/parent/v1/volume", post_volume)
    app.router.add_get("/parent/v1/papers/inbox", get_paper_inbox)
    app.router.add_get("/parent/v1/papers/reports", get_paper_reports)
    app.router.add_post("/parent/v1/papers/report", post_paper_report)
    app.router.add_post("/parent/v1/papers/purge", post_paper_purge)
    app.router.add_post("/parent/v1/papers/delete-all", post_paper_delete_all)
    app.router.add_post("/parent/v1/papers/revision", post_paper_revision)
    app.router.add_get("/parent/v1/weak-points", get_weak_points)
    app.router.add_post("/parent/v1/weak-points", post_weak_points)
    app.router.add_post("/parent/v1/practice/extra", post_practice_extra)
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
