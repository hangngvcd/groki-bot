import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp import gbot_dialogue, voice_tools
from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge


@pytest.mark.parametrize(
    "text,expected",
    [
        ("你可以說普通話嗎？", "cmn"),
        ("請用普通话", "cmn"),
        ("你而家仲用緊廣東話", "cmn"),
        ("仲係講緊廣東話", "cmn"),
        ("仲講緊廣東話", "cmn"),
        ("Mandarin please", "cmn"),
        ("講返廣東話", "yue"),
        ("講廣東話啦", "yue"),
        ("唔好講普通話", "yue"),
        ("Cantonese please", "yue"),
        ("普通話默書幾時", None),
        ("今日普通話堂好悶", None),
    ],
)
def test_language_requests(text, expected):
    assert gbot_dialogue.detect_language_request(text) == expected


def test_child_tools(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    executor = voice_tools.VoiceToolExecutor()
    assert {"battery_status", "get_current_datetime"} <= {
        t["name"] for t in executor.declarations()
    }


def test_hkt_time(monkeypatch):
    import datetime as dt

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 30, 17, 47, tzinfo=dt.timezone.utc).astimezone(tz)

    monkeypatch.setattr(dt, "datetime", Frozen)
    result = voice_tools.VoiceToolExecutor._dispatch_get_datetime()
    assert result["timezone"] == "Asia/Hong_Kong"
    assert result["spoken_zh"] == "而家係香港時間十月三十一號星期六，凌晨一點四十七分。"


@pytest.mark.parametrize(
    "percent,age,connected,stale",
    [
        (75, 10, True, False),
        (20, 0, True, False),
        (None, 0, True, True),
        (60, 600, True, False),
        (60, 601, True, True),
        (60, 0, False, True),
    ],
)
def test_battery(monkeypatch, percent, age, connected, stale):
    from stackchan_mcp.debug_status import DebugStatus

    status = DebugStatus(clock=lambda: 1000)
    status.device_connected_flag = connected
    status.device_battery_percent = percent
    status.device_battery_at = 1000 - age
    monkeypatch.setattr(voice_tools, "get_debug_status", lambda: status)
    result = voice_tools.VoiceToolExecutor._dispatch_battery_status()
    assert result["stale"] is stale
    if stale:
        assert (
            result["spoken"]
            == "我而家讀唔到自己有幾多電，可能係插住電用緊。想知就問吓爸爸媽媽睇吓啦。"
        )
    else:
        assert result["spoken"].startswith(f"我仲有大約{percent}%電。")
        assert ("有啲少電" in result["spoken"]) == (percent <= 20)


@pytest.mark.asyncio
async def test_bridge_language_updates(monkeypatch):
    from stackchan_mcp import gbot_voice_bridge as module

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setattr(module, "mandarin_enabled", lambda: True)
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
    )
    b._base_instructions = "base"
    b._send = AsyncMock()

    async def say(text, item):
        await b._handle_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": text,
                "item_id": item,
            }
        )

    await say("你可以說普通話嗎？", "a")
    await say("你可以說普通話嗎？", "a")
    await say("你而家仲用緊廣東話", "b")
    await say("你而家仲用緊廣東話呀", "b")
    assert b._send.await_count == 2
    assert b._send.call_args.args[0]["session"][
        "instructions"
    ] == "base" + gbot_dialogue.language_mode_block("cmn")
    await say("講返廣東話", "c")
    assert b._language_mode == "yue"
    monkeypatch.setattr(module, "mandarin_enabled", lambda: False)
    await say("請講普通話", "d")
    assert b._send.await_count == 3
    assert b.metrics["language_switches"] == 2


@pytest.mark.asyncio
async def test_parent_disabling_mandarin_resets_mode(monkeypatch):
    from stackchan_mcp import gbot_voice_bridge as module

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setattr(module, "mandarin_enabled", lambda: True)
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
    )
    b._base_instructions = "base"
    b._send = AsyncMock()
    b.running = True
    await b._handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "請講普通話",
            "item_id": "a",
        }
    )
    b._on_settings_changed({"mandarin": False}, {"mandarin"})
    await asyncio.gather(*list(b._tools))
    updates = [
        call.args[0]
        for call in b._send.await_args_list
        if call.args[0]["type"] == "session.update"
    ]
    assert len(updates) == 2
    assert b._language_mode == "yue"
    assert updates[-1]["session"][
        "instructions"
    ] == "base" + gbot_dialogue.language_mode_block("yue")


@pytest.mark.asyncio
async def test_language_update_failure_is_contained(monkeypatch):
    from stackchan_mcp import gbot_voice_bridge as module

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setattr(module, "mandarin_enabled", lambda: True)
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
    )
    b._send = AsyncMock(side_effect=RuntimeError("closed"))
    await b._handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "請講普通話",
            "item_id": "a",
        }
    )
    assert b._send.await_count == 1


@pytest.mark.parametrize(
    "hour,minute,spoken",
    [
        (0, 0, "凌晨十二點正"),
        (5, 1, "凌晨五點零一分"),
        (6, 9, "朝早六點零九分"),
        (11, 10, "朝早十一點十分"),
        (12, 0, "中午十二點正"),
        (13, 30, "下晝一點三十分"),
        (17, 59, "下晝五點五十九分"),
        (18, 20, "夜晚六點二十分"),
        (23, 47, "夜晚十一點四十七分"),
    ],
)
def test_time_periods(monkeypatch, hour, minute, spoken):
    import datetime as dt

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 31, hour, minute, tzinfo=tz)

    monkeypatch.setattr(dt, "datetime", Frozen)
    result = voice_tools.VoiceToolExecutor._dispatch_get_datetime()
    assert result["spoken_zh"].endswith(spoken + "。")
    assert result["weekday"] == "Saturday"
    assert result["datetime"].endswith("+08:00")


@pytest.mark.asyncio
async def test_battery_dispatch_and_metadata(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    executor = voice_tools.VoiceToolExecutor()
    try:
        result = await executor.execute("battery_status", {})
        assert result["ok"] and result["stale"]
        assert result["percent"] is None
        assert executor.metadata("battery_status") == voice_tools.ToolMetadata(
            False, None, 2.0
        )
        instructions = executor.instructions()
        assert "讀 spoken_zh" in instructions and "讀 spoken" in instructions
        assert "問時間、問電量唔係改設定" in instructions
        assert "呢樣要問媽媽或者爸爸幫你改㗎。" in instructions
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_non_child_never_switches_language(monkeypatch):
    from stackchan_mcp import gbot_voice_bridge as module

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "0")
    monkeypatch.setattr(module, "mandarin_enabled", lambda: True)
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
    )
    b._send = AsyncMock()
    await b._handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "請講普通話",
            "item_id": "a",
        }
    )
    b._send.assert_not_awaited()


def test_battery_unknown_reason_flows_to_spoken_and_parent(monkeypatch):
    from stackchan_mcp import debug_status as ds

    status = ds.DebugStatus()
    status.on_device_connected("dev")
    status.on_device_battery_unknown("usb_pinned")
    monkeypatch.setattr(voice_tools, "get_debug_status", lambda: status)
    result = voice_tools.VoiceToolExecutor._dispatch_battery_status()
    assert result["stale"] and result["reason"] == "usb_pinned"
    assert "插住電" in result["spoken"]
    assert status.snapshot()["device"]["battery_unknown_reason"] == "usb_pinned"
    status.on_device_battery(55)
    result = voice_tools.VoiceToolExecutor._dispatch_battery_status()
    assert not result["stale"] and result["percent"] == 55
    assert "55%" in result["spoken"]
    assert status.device_battery_unknown_reason is None


def test_battery_unknown_reason_sanitized():
    from stackchan_mcp import debug_status as ds

    status = ds.DebugStatus()
    status.on_device_battery_unknown("<script>")
    assert status.device_battery_unknown_reason == "unknown"
    assert status.device_battery_percent is None
