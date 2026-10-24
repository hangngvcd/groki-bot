#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Record one short spoken sample per Grok Bot realtime voice (no device, no tools).

Runs a separate one-off voice session per voice through the local login helper,
asks the model to read a fixed line aloud, and saves the 24 kHz PCM16 reply as a
WAV. Credentials/agent IDs stay in process memory and are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import plistlib
import wave

LINE = "娜娜你好呀！我係鈴鈴。今日功課多唔多呀？我哋一齊慢慢做，唔使急。"
INSTRUCTIONS = (
    "你係鈴鈴，一個陪香港小朋友做功課嘅溫柔助手。只講香港粵語（廣東話口語），"
    "唔好講普通話或者英文。用戶會畀你一句句子，你要用自然、親切、慢慢嘅語氣"
    "一字不改咁讀出嚟，唔好加任何其他說話，唔好解釋，唔好重複。唔好用任何工具。"
)
# Docs: https://docs.x.ai/developers/model-capabilities/audio/voice
VOICES = ["ara", "eve", "leo", "rex", "sal", "carina", "zagan", "helix", "orion", "luna",
          "iris", "altair", "zenith", "perseus", "helios", "lux", "kepler", "rigel", "cosmo",
          "celeste", "ursa", "sirius", "lumen", "castor", "naksh", "atlas", "aurora", "liora"]
SAFE_KEYS = {"STACKCHAN_GBOT_VOICE_BOT_ID", "STACKCHAN_TOOL_BOT_ID",
             "STACKCHAN_GBOT_VOICE_URL", "STACKCHAN_GBOT_VOICE_LANGUAGE"}


def load_env(plist_path):
    if plist_path.exists():
        data = plistlib.loads(plist_path.read_bytes())
        for name, value in data.get("EnvironmentVariables", {}).items():
            if name in SAFE_KEYS:
                os.environ.setdefault(name, str(value))


async def record(voice, out_dir, timeout):
    from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge

    os.environ["STACKCHAN_GBOT_VOICE_NAME"] = voice
    pcm = bytearray()
    done = asyncio.Event()
    dead = asyncio.Event()
    transcript = []

    async def on_audio(chunk):
        pcm.extend(chunk)

    async def on_turn_complete():
        done.set()

    async def noop(*_a, **_k):
        return None

    async def on_dead():
        dead.set()

    async def on_text(text):
        transcript.append(text)

    class SampleBridge(GbotVoiceBridge):
        async def _local(self, path, payload=None):
            result = await super()._local(path, payload)
            if path == "/session":
                result = {**result, "instructions": INSTRUCTIONS, "tools": []}
            return result

        async def _handle_event(self, event):
            if event.get("type") == "session.updated":
                echoed = (event.get("session") or {}).get("voice")
                row["voice_echo"] = echoed if isinstance(echoed, str) and len(echoed) < 40 else None
            await super()._handle_event(event)

    row = {"voice": voice}
    bridge = SampleBridge(on_audio=on_audio, on_turn_complete=on_turn_complete,
                          on_interrupt=noop, on_session_dead=on_dead, on_text=on_text,
                          local_tools=False, conversation_idle_timeout_s=0)
    try:
        await bridge.start()
        if not await bridge.wait_connected(10):
            row["error"] = "no_session_updated"
            return row
        await bridge.send_text("請用香港粵語讀出以下句子，一字不改：「" + LINE + "」")
        waiter = asyncio.ensure_future(done.wait())
        dier = asyncio.ensure_future(dead.wait())
        await asyncio.wait({waiter, dier}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel(); dier.cancel()
        if not done.is_set():
            row["error"] = "session_dead" if dead.is_set() else "timeout"
        errs = bridge.metrics.get("server_errors")
        if errs:
            row["server_errors"] = errs
    except Exception as exc:  # never serialize exception text
        row["error"] = type(exc).__name__
    finally:
        await bridge.stop()
    if pcm:
        path = out_dir / f"{voice}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(bytes(pcm))
        row.update(path=str(path), seconds=round(len(pcm) / 48000, 2))
    row["transcript"] = "".join(transcript)[:200]
    return row


async def main_async(args):
    load_env(args.launchd_plist)
    os.environ["STACKCHAN_GBOT_VOICE_TOOLS"] = "0"
    if args.language:
        os.environ["STACKCHAN_GBOT_VOICE_LANGUAGE"] = args.language
    logging.disable(logging.CRITICAL)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for voice in (args.voices.split(",") if args.voices else VOICES):
        row = await record(voice, args.output_dir, args.timeout)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        rows.append(row)
        await asyncio.sleep(args.pause)
    (args.output_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path,
                   default=Path.home() / "Downloads/lingling-voice-samples/grok")
    p.add_argument("--voices", help="comma list; default all documented voices")
    p.add_argument("--language", help="override STACKCHAN_GBOT_VOICE_LANGUAGE")
    p.add_argument("--timeout", type=float, default=40)
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--launchd-plist", type=Path,
                   default=Path.home() / "Library/LaunchAgents/local.xiaoya.gateway.plist")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
