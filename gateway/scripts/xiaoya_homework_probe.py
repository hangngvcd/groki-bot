#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic homework turns through real native voice/tools, without a board.

Run from gateway with uv run python scripts/xiaoya_homework_probe.py --help.
Inputs must be synthetic; keep inputs and results outside the git worktree.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
import wave

from probe_gbot_voice import read_pcm
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge
from stackchan_mcp.gbot_voice_proxy import GbotVoiceProxy
from stackchan_mcp.opus_codec import _patch_find_library_for_brew


async def run(args):
    target = os.getenv("STACKCHAN_TOOL_BOT_ID", "").strip()
    if (not os.getenv("STACKCHAN_TOOL_BOT", "").strip() or not target
            or target != os.getenv("STACKCHAN_GBOT_VOICE_BOT_ID", "").strip()):
        raise ValueError(
            "Native send_task requires STACKCHAN_TOOL_BOT and matching nonempty "
            "STACKCHAN_TOOL_BOT_ID / STACKCHAN_GBOT_VOICE_BOT_ID. "
            "Start the login helper with the same environment (it does not read .env)."
        )
    repo = Path(__file__).resolve().parents[2]
    for path in (args.script, args.output_dir):
        if path.resolve().is_relative_to(repo):
            raise ValueError("Keep synthetic inputs and reply artifacts outside the git worktree.")
    files = sorted(
        (p for p in args.script.glob("*.wav") if re.fullmatch(r"\d+-.*\.wav", p.name)),
        key=lambda p: (int(p.name.split("-")[0]), p.name),
    )
    if not files:
        raise ValueError("--script must contain numbered WAVs (01-*.wav, 02-*.wav, ...).")
    # Policy is forced only for this process. No local_tools=False: native tools stay enabled.
    os.environ.update({
        "STACKCHAN_CHILD_PROFILE": "1", "STACKCHAN_USB_DISABLE": "1",
        "STACKCHAN_BUTTON_ONLY_LISTEN": "1", "STACKCHAN_WAKE_WORD": "0",
    })
    os.environ.pop("STACKCHAN_USB_TRANSPORT", None)
    _patch_find_library_for_brew()
    from opuslib import APPLICATION_AUDIO, Decoder, Encoder

    microphone = Encoder(16000, 1, APPLICATION_AUDIO)
    speaker = Decoder(24000, 1)
    current = None
    audio = bytearray()
    bridge = None
    stopped = False
    last_activity = 0.0
    accepted = 0
    landed = 0
    completed = 0
    completion_after_landed = False
    completion_responses = set()

    async def transcript(event):
        if current is not None:
            text = event.get("transcript", "")
            if event["type"] == "conversation.item.input_audio_transcription.completed":
                current["user_transcript"] = text
            else:
                current["assistant_transcripts"].append(text)

    class ObservedBridge(GbotVoiceBridge):
        # Instrument existing boundaries; record only tool names and fixed outcomes.
        async def _send(self, event):
            nonlocal accepted, landed, last_activity, completion_after_landed
            item = event.get("item", {})
            if current is not None and item.get("type") == "function_call_output":
                output = json.loads(item["output"])
                relay = output.get("relay")
                if current["tool_calls"]:
                    if relay in {"accepted", "refused", "unreachable"}:
                        current["tool_calls"][-1]["relay"] = relay
                if relay == "accepted":
                    accepted += 1
                last_activity = time.monotonic()
            result = await super()._send(event)
            if (event.get("type") == "async_update.add"
                    and event.get("async_update", {}).get("topic") == "work_landed"):
                landed += 1
                completion_after_landed = False
                last_activity = time.monotonic()
            return result

        def _tool_outcome(self, name, outcome):
            if current is not None:
                current["tool_calls"].append({
                    "name": self._safe_tool_name(name), "outcome": outcome,
                })
            super()._tool_outcome(name, outcome)

        async def _handle_event(self, event):
            nonlocal completed, last_activity, completion_after_landed
            response_id = event.get("response", {}).get("id")
            if event.get("type") == "response.created" and landed:
                completion_responses.add(response_id)
            await super()._handle_event(event)
            if event.get("type") == "response.done":
                completed += 1
                completion_after_landed = response_id in completion_responses
                last_activity = time.monotonic()

    def factory(**kwargs):
        nonlocal bridge
        bridge = ObservedBridge(
            **kwargs, on_transcript=transcript, conversation_idle_timeout_s=3600,
        )
        return bridge

    async def sink(message):
        nonlocal stopped, last_activity
        if isinstance(message, bytes):
            audio.extend(speaker.decode(message, 1440))
            stopped = False
            last_activity = time.monotonic()
        else:
            event = json.loads(message)
            if event.get("type") == "tts":
                stopped = event.get("state") == "stop"
                last_activity = time.monotonic()

    async def feed(pcm):
        for index in range(0, len(pcm), 1920):
            chunk = pcm[index:index + 1920].ljust(1920, b"\0")
            if not await proxy.send_device_binary(microphone.encode(chunk, 960)):
                raise RuntimeError("Device audio rejected")
            await asyncio.sleep(0.06)

    async def turn(path):
        nonlocal current, audio, stopped, accepted, landed, completed
        nonlocal last_activity, completion_after_landed
        current = {
            "input_file": path.name, "user_transcript": "",
            "assistant_transcripts": [], "tool_calls": [],
        }
        summary["turns"].append(current)
        audio = bytearray()
        stopped = completion_after_landed = False
        accepted = landed = completed = 0
        completion_responses.clear()
        started = last_activity = time.monotonic()
        try:
            async with asyncio.timeout(args.timeout):
                if not await proxy.send_device_json({"type": "listen", "state": "detect"}):
                    raise RuntimeError("Device listen rejected")
                await feed(read_pcm(path))
                await feed(bytes(1920 * 20))
                # Wait for drained TTS and response.done. An accepted task also
                # needs work_landed and its spoken follow-up, within this timeout.
                while True:
                    waiting_for_task = accepted > 0 and (
                        landed < accepted or not completion_after_landed
                    )
                    if (completed and stopped and not waiting_for_task
                            and not bridge._response_in_progress
                            and not bridge._response_requested
                            and not bridge._pending_updates
                            and not any(not task.done() for task in bridge._tools)
                            and time.monotonic() - last_activity >= 1.5):
                        break
                    if not proxy.connected:
                        raise RuntimeError("Voice disconnected")
                    await asyncio.sleep(0.1)
            current["status"] = "complete"
        except TimeoutError:
            current["status"] = "timeout"
            summary["passed"] = False
        finally:
            current["elapsed"] = round(time.monotonic() - started, 3)
            with wave.open(str(args.output_dir / (path.stem + "-reply.wav")), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(audio)
        return current["status"] == "complete"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"passed": True, "turns": []}
    proxy = GbotVoiceProxy(bridge_factory=factory)
    try:
        if not await proxy.start(SimpleNamespace(device_id="synthetic-homework"), {}, sink):
            raise RuntimeError("Voice unavailable: check Grok Bot sign-in and the running login helper.")
        if bridge._native:
            try:
                await asyncio.wait_for(bridge._native.connected.wait(), min(args.timeout, 15))
            except TimeoutError:
                pass
        summary["native_ready"] = bool(bridge._native and bridge._native.ready)
        if not summary["native_ready"] and args.allow_not_ready:
            print("Native channel not ready: continuing (--allow-not-ready) to check honest failures.")
        elif not summary["native_ready"]:
            raise RuntimeError(
                "Native channel not ready: run the login helper desktop/native side "
                "and sign in to Grok Bot; check the matching agent environment."
            )
        for path in files:
            if not await turn(path):
                break  # A late reply must not be attributed to the next child turn.
    except Exception as error:
        summary["passed"] = False
        summary["error_type"] = type(error).__name__
        raise
    finally:
        current = None
        await proxy.stop()
        (args.output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    print("Results written to output directory; review replies and summary.json.")
    return 0 if summary["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=60,
                        help="Seconds per turn, including desktop completion (default: 60)")
    parser.add_argument("--allow-not-ready", action="store_true",
                        help="Run even if the desktop channel is not ready (tests honest failure replies)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        code = asyncio.run(run(args))
    except ValueError as error:
        parser.exit(2, str(error) + "\n")
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
    except Exception as error:
        # Never print raw service errors, bodies, IDs or credentials.
        parser.exit(1, "Probe failed (" + type(error).__name__ + "); check login helper/app.\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
