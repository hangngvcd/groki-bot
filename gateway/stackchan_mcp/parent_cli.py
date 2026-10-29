# SPDX-License-Identifier: MIT
"""Parent CLI: stdlib HTTP client; MCP dependencies load only for `mcp`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ParentClient:
    def __init__(self, url="http://127.0.0.1:18772"):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Parent API URL 必須係本機 / must use loopback HTTP")
        self.url = url.rstrip("/")

    def call(self, endpoint, values=None):
        try:
            token = os.getenv("STACKCHAN_PARENT_TOKEN")
            if not token:
                token = (
                    Path(
                        os.getenv(
                            "STACKCHAN_PARENT_TOKEN_FILE", "~/.stackchan/parent_token"
                        )
                    )
                    .expanduser()
                    .read_text()
                    .strip()
                )
            request = Request(
                self.url + "/parent/v1/" + endpoint,
                data=None if values is None else json.dumps(values).encode(),
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
            )
            with build_opener(ProxyHandler({}), NoRedirect()).open(
                request, timeout=3
            ) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 401:
                raise RuntimeError(
                    "家長認證失敗 / parent API unauthorized (401)"
                ) from None
            detail = ""
            try:
                detail = json.loads(exc.read() or b"{}").get("error") or ""
            except (OSError, ValueError, AttributeError):
                pass
            raise RuntimeError(
                f"家長 API 請求失敗 / parent API error ({exc.code})"
                + (f": {detail}" if detail else "")
            ) from None
        except (OSError, URLError):
            raise RuntimeError(
                "gateway 未行 / parent API 未開 (STACKCHAN_PARENT_API=1)"
            ) from None


def cli_value(key, value):
    from .parent_settings import SCHEMA

    if SCHEMA.get(key, {}).get("type") == "string":
        return value
    if SCHEMA.get(key, {}).get("type") == "boolean":
        if value.lower() not in {"on", "off", "true", "false", "1", "0"}:
            raise ValueError("布林值請用 on/off/true/false/1/0 / invalid boolean")
        return value.lower() in {"on", "true", "1"}
    if key == "battery_curve_raw":
        return None if value == "null" else [int(x) for x in value.split(",")]
    try:
        return int(value)
    except ValueError:
        raise ValueError("請輸入整數 / expected integer") from None


async def run_mcp(client):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("xiaoya-parent")

    @server.tool()
    async def xiaoya_usage(date: str | None = None) -> dict:
        """Read local daily usage counters for a Hong Kong date (last 14 days)."""
        try:
            endpoint = "usage" + ("?" + urlencode({"date": date}) if date else "")
            return await asyncio.to_thread(client.call, endpoint)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    async def xiaoya_status() -> dict:
        """Read current device and gateway status."""
        try:
            return await asyncio.to_thread(client.call, "status")
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    async def xiaoya_get_settings() -> dict:
        """Read parent settings and firmware support."""
        try:
            return await asyncio.to_thread(client.call, "settings")
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    async def xiaoya_set_setting(key: str, value: str) -> dict:
        """Change one parent setting after the parent confirmed it.

        key: volume (0-100), rest_after_min (1-60), camera_presence,
        notifications or mandarin (on/off; mandarin = 鈴鈴 may speak 普通話
        when 娜娜 asks, default on). value is given as text, e.g. "40" or "off".
        """
        try:
            parsed = cli_value(key, str(value))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            return await asyncio.to_thread(client.call, "settings", {key: parsed})
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    async def homework(values=None):
        try:
            return await asyncio.to_thread(client.call, "homework", values)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    async def xiaoya_homework_list() -> dict:
        """Read today's homework list, how many 娜娜 has done, and 鈴鈴's form."""
        return await homework()

    @server.tool()
    async def xiaoya_homework_add(titles: list[str]) -> dict:
        """Add homework items to today's list, only when the parent says so.

        Progress is NOT reported here: 娜娜 marks items done herself and the
        gateway recomputes 鈴鈴's form.  Adding never lowers the form.
        """
        return await homework({"add": titles})

    @server.tool()
    async def xiaoya_homework_remove(items: list[str]) -> dict:
        """Remove homework items (by #id or title) when the parent says so."""
        return await homework({"remove": items})

    @server.tool()
    async def xiaoya_homework_redo(items: list[str], reason: str = "") -> dict:
        """Mark items (by #id or title) as needing a redo, when the parent asks.

        The item becomes pending again; 鈴鈴's form does not go down.
        """
        return await homework({"redo": items, "reason": reason})

    @server.tool()
    async def xiaoya_get_volume() -> dict:
        """Read 鈴鈴's speaker volume (0-100), what the device last reported and
        its last reset reason (e.g. brownout)."""
        try:
            return await asyncio.to_thread(client.call, "volume")
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    async def xiaoya_set_volume(volume: int) -> dict:
        """Set 鈴鈴's speaker volume 0-100 (default 40) after the parent asked.

        Applied immediately if she is connected, otherwise on her next connect.
        """
        if type(volume) is not int or not 0 <= volume <= 100:
            return {"ok": False, "error": "音量要係 0 至 100 / volume must be 0..100"}
        try:
            return await asyncio.to_thread(client.call, "volume", {"volume": volume})
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    async def xiaoya_battery_cal(action: str, csv: str | None = None, raw: list[int] | None = None, interval_s: int = 30) -> dict:
        """Parent discharge calibration: start/stop/fit/apply/reset. Fit only proposes."""
        values = {"action": action, "interval_s": interval_s}
        if csv is not None:
            values["csv"] = csv
        if raw is not None:
            values["raw"] = raw
        try:
            return await asyncio.to_thread(client.call, "battery-cal", values)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    async def xiaoya_replay_intro() -> dict:
        """Ask 鈴鈴 to play her pre-recorded self-intro again (parent asked)."""
        try:
            return await asyncio.to_thread(client.call, "intro", {})
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    await server.run_stdio_async()


def main(argv=None):
    parser = argparse.ArgumentParser(description="小芽家長助手（鈴鈴）/ parent settings")
    parser.add_argument("--url", default="http://127.0.0.1:18772")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "usage", "settings", "set", "homework", "intro", "volume", "mcp"):
        command = commands.add_parser(name)
        command.add_argument("--url", default=argparse.SUPPRESS)
        if name == "usage":
            command.add_argument("--date")
        if name in {"status", "usage"}:
            command.add_argument("--text", action="store_true")
        if name == "set":
            command.add_argument("key")
            command.add_argument("value")
        if name == "volume":
            command.add_argument("value", nargs="?", type=int)
        if name == "homework":
            command.add_argument("action", nargs="?", choices=("list", "add", "remove", "redo"), default="list")
            command.add_argument("--reason", default="")
            command.add_argument("items", nargs="*")
    battery = commands.add_parser("battery-cal")
    battery.add_argument("action", choices=("start", "stop", "fit", "apply", "reset"))
    battery.add_argument("csv", nargs="?")
    battery.add_argument("--interval", type=int, default=30)
    battery.add_argument("--raw")
    threshold = commands.add_parser("battery-threshold")
    threshold.add_argument("pct", type=int)
    args = parser.parse_args(argv)
    try:
        client = ParentClient(args.url)
        if args.command == "battery-threshold":
            data = client.call("settings", {"battery_low_threshold_pct": args.pct})
            print(json.dumps(data, indent=2))
            return 0 if data.get("ok") else 1
        if args.command == "battery-cal":
            if args.action == "apply" and bool(args.csv) == bool(args.raw):
                raise ValueError("apply requires either CSV or --raw r0,..,r5")
            values = {"action": args.action, "interval_s": args.interval}
            if args.csv:
                values["csv"] = args.csv
            if args.raw:
                values["raw"] = [int(x) for x in args.raw.split(",")]
            data = client.call("battery-cal", values)
            print(json.dumps(data, indent=2))
            return 0 if data.get("ok") else 1
        if args.command == "mcp":
            asyncio.run(run_mcp(client))
            return 0
        if args.command == "usage":
            from .usage_stats import format_usage

            endpoint = "usage" + ("?" + urlencode({"date": args.date}) if args.date else "")
            data = client.call(endpoint)
            print(format_usage(data["usage"], data["date"]) if args.text else json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.command == "volume":
            if args.value is not None and not 0 <= args.value <= 100:
                raise ValueError("音量要係 0 至 100 / volume must be 0..100")
            data = client.call("volume", None if args.value is None else {"volume": args.value})
            if data.get("ok"):
                where = {"now": "已即時套用", "next_connect": "鈴鈴下次連線時套用"}.get(data.get("applies"), "")
                print(f"音量 {data['volume']}" + (f"（{where}）" if where else "")
                      + f"；裝置回報 {data.get('device_volume')}；上次重啟原因 {data.get('last_reset_reason')}")
            else:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0 if data.get("ok") else 1
        if args.command == "intro":
            data = client.call("intro", {})
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0 if data.get("ok") else 1
        if args.command == "homework":
            if args.action == "list":
                if args.items:
                    raise ValueError("用法：homework [list|add|remove|redo] [項目...]")
                values = None
            elif not args.items:
                raise ValueError(f"用法：homework {args.action} 項目... / usage: homework {args.action} ITEM...")
            else:
                values = {args.action: args.items}
                if args.action == "redo" and args.reason:
                    values["reason"] = args.reason
            data = client.call("homework", values)
            if data.get("ok"):
                forms = data["forms"]
                for item in forms["items"]:
                    print(f"#{item['id']} {'✓' if item['done'] else '·'} {item['title']}")
                print(f"今日 {forms['done']}/{forms['total']} 項；鈴鈴：F{forms['form']} {forms['form_name_zh']}")
            else:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0 if data.get("ok") else 1
        values = (
            {args.key: cli_value(args.key, args.value)}
            if args.command == "set"
            else None
        )
        data = client.call(
            "settings" if args.command == "set" else args.command, values
        )
        if args.command == "status" and args.text:
            status = data["status"]
            print(status["summary_zh"])
            helper = status["voice"].get("login_helper") or {}
            print(f"語音後端：{status['voice']['backend']}"
                  + (f"；login helper：{'有回應' if helper.get('reachable') else '冇回應'}"
                     if status["voice"]["backend"] == "gbot" else ""))
            if status["voice"].get("last_error"):
                print(f"最近語音錯誤：{status['voice']['last_error']}")
            print(f"兒童模式：{'開' if status['child_profile'] else '關'}")
            settings = status["settings"]
            print(
                f"設定：音量 {settings['volume']}；休息 {settings['rest_after_min']} 分鐘；"
                f"鏡頭偵測 {'開' if settings['camera_presence'] else '關'}；"
                f"通知 {'開' if settings['notifications'] else '關'}；"
                f"普通話 {'開' if settings.get('mandarin', True) else '關'}"
            )
            pending = [
                k
                for k, v in status.get("device_support", {}).items()
                if not v.get("applied_to_device") and not v.get("applied_in_gateway")
            ]
            if pending:
                print("未推到部機（韌體未支援，只係儲存咗）：" + "、".join(pending))
            print("今日用量：stackchan-parent usage --text")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
