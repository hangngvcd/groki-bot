# SPDX-License-Identifier: MIT
"""Homework relay seam; catalog coverage: test_child_profile.py::test_gbot_receives_only_child_catalog."""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from test_gbot_voice_tools import make
from stackchan_mcp.voice_tools import VoiceToolExecutor

REFUSED = "我而家攞唔到，唔好意思。你可以遲啲再問我，或者問吓媽媽或者爸爸。"
OFFLINE = "我而家連唔到電腦，攞唔到資料。等陣再試，或者問吓媽媽或者爸爸。"


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("case", ["not_ready", "search_not_ready", "refused", "unreachable", "invalid_input", "accepted"])
async def test_homework_native_relay(monkeypatch, child, case):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1" if child else "0")
    bridge = make(monkeypatch)
    native = (
        {"kind": "invalid_input", "output": {"relay": "accepted"}}
        if case == "invalid_input"
        else {"kind": "served", "output": {
            "relay": case, "spoken": "untrusted native wording", "errorClass": "network"
        }}
    )
    bridge._local = AsyncMock(return_value=native)
    name = "search_conversations" if case == "search_not_ready" else "send_task"
    if name == "search_conversations":
        bridge._remote_tools[name] = {"parameters": {"type": "object"}}
    not_ready = case in {"not_ready", "search_not_ready"}
    bridge._native.ready = not not_ready
    try:
        await bridge._handle_event({"type": "input_audio_buffer.speech_started"})
        await bridge._tool({
            "name": name, "call_id": "homework",
            "arguments": '{"request":"今日有咩功課？"}',
        })
        output = next(
            json.loads(call.args[0]["item"]["output"])
            for call in bridge._send.call_args_list
            if call.args[0]["type"] == "conversation.item.create"
        )
        if not_ready:
            bridge._local.assert_not_awaited()
        else:
            bridge._local.assert_awaited_once()
        if case == "accepted":
            assert output == {"relay": "accepted"}
        else:
            if name == "send_task":
                assert output["relay"] == ("refused" if case == "refused" else "unreachable")
            else:
                assert output["ok"] is False
            expected = (
                REFUSED if case == "refused" else OFFLINE
            ) if child else (
                "桌面助手没有受理这项任务。" if case == "refused" else
                "桌面助手暂时未连上，请稍后再试。" if not_ready else
                "桌面助手暂时无法受理任务，请稍后再试。"
            )
            assert output["spoken"] == expected
    finally:
        await bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
async def test_child_homework_instructions(monkeypatch, child):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1" if child else "0")
    bridge = make(monkeypatch)
    executor = VoiceToolExecutor(instruction_provider=lambda **kwargs: "")
    bridge._executor = executor
    try:
        instructions = bridge._provider_tool_instructions()
        for phrase in ("我睇吓先", "收到", "relay=accepted", "呢樣要問爸爸媽媽", "唔係", "小朋友新輸入"):
            if child:
                assert phrase in instructions
            elif phrase != "relay=accepted":
                assert phrase not in instructions
    finally:
        await bridge.stop()


@pytest.mark.skipif(not shutil.which("bash"), reason="bash unavailable")
def test_synthetic_homework_script_dry_run(tmp_path):
    script = Path(__file__).parents[1] / "scripts/xiaoya_make_homework_wavs.sh"
    result = subprocess.run(
        ["bash", str(script), "--dry-run", str(tmp_path)],
        check=True, capture_output=True, text=True,
    )
    for line in (
        "今日有咩功課？", "仲有咩未做？", "數學做完喇", "唔係",
        "數學做完喇", "係", "幫我話俾媽媽知我數學卡住", "係",
        "幫我改吓啱唔啱，俾個分我",
    ):
        assert line in result.stdout
    assert result.stdout.count("say -v") == 9
    assert result.stdout.count("afconvert") == 9
