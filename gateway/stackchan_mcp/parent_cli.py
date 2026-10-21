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
from urllib.parse import urlsplit
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
    if key in {"camera_presence", "notifications"}:
        if value.lower() not in {"on", "off", "true", "false", "1", "0"}:
            raise ValueError("布林值請用 on/off/true/false/1/0 / invalid boolean")
        return value.lower() in {"on", "true", "1"}
    try:
        return int(value)
    except ValueError:
        raise ValueError("請輸入整數 / expected integer") from None


async def run_mcp(client):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("xiaoya-parent")

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

        key: volume (0-100), rest_after_min (1-60), camera_presence or
        notifications (on/off). value is given as text, e.g. "40" or "off".
        """
        try:
            parsed = cli_value(key, str(value))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            return await asyncio.to_thread(client.call, "settings", {key: parsed})
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    await server.run_stdio_async()


def main(argv=None):
    parser = argparse.ArgumentParser(description="小芽家長助手 / parent settings")
    parser.add_argument("--url", default="http://127.0.0.1:18772")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "settings", "set", "mcp"):
        command = commands.add_parser(name)
        command.add_argument("--url", default=argparse.SUPPRESS)
        if name == "status":
            command.add_argument("--text", action="store_true")
        if name == "set":
            command.add_argument("key")
            command.add_argument("value")
    args = parser.parse_args(argv)
    try:
        client = ParentClient(args.url)
        if args.command == "mcp":
            asyncio.run(run_mcp(client))
            return 0
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
                f"通知 {'開' if settings['notifications'] else '關'}"
            )
            pending = [k for k, v in status.get("device_support", {}).items() if not v.get("applied_to_device")]
            if pending:
                print("未推到部機（韌體未支援，只係儲存咗）：" + "、".join(pending))
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
