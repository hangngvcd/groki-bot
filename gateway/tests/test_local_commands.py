from unittest.mock import AsyncMock
import pytest
from stackchan_mcp.local_commands import LocalCommands, match
from stackchan_mcp.parent_settings import ParentSettings
from stackchan_mcp.xiaoya_forms import XiaoyaForms
from stackchan_mcp.xiaoya_games import XiaoyaGames
from stackchan_mcp.usage_stats import UsageStats


@pytest.mark.parametrize('text,name', [('計時六十分鐘','timer'), ('计时 12 分钟！','timer'), ('仲有幾耐','remaining'), ('停','stop'), ('停止計時','stop'), ('小休','break'), ('休息一阵','break'), ('玩游戏','game'), ('仲有咩功课','homework'), ('做完中文','done'), ('你好','hello'), ('拜拜','bye')])
def test_hits(text, name):
    assert match(text).name == name


@pytest.mark.parametrize('text', ['計時零分鐘','計時61分鐘','計時十一十分鐘','計時2分鐘再玩遊戲','停一停','我想小休','玩遊戲好嗎','你好拜拜','做完','做完中文，仲有英文',''])
def test_miss(text):
    assert match(text) is None


@pytest.mark.asyncio
async def test_hook(tmp_path):
    settings = ParentSettings(tmp_path/'settings')
    forms = XiaoyaForms(tmp_path/'forms')
    games = XiaoyaGames(path=tmp_path/'games')
    usage = UsageStats(tmp_path/'usage')
    speak = AsyncMock()
    local = LocalCommands(settings=settings, forms=forms, games=games, usage=usage, speak=speak, clock=lambda: 100)
    assert await local.handle_before_session('玩遊戲')
    assert not games.active
    assert usage.snapshot()['local_handled_count'] == 1
    assert await local.handle_before_session('計時三分鐘')
    assert local.deadline == 280
    assert await local.handle_before_session('仲有幾耐')
    assert await local.handle_before_session('停')
    assert local.deadline is None
    forms.add_items(['中文','中文閱讀'], source='parent')
    assert await local.handle_before_session('做完中') is None
    assert await local.handle_before_session('做完中文')
    assert forms.snapshot()['done'] == 1
    settings.set('local_commands_enabled', False, source='parent')
    before = usage.snapshot()['local_handled_count']
    assert await local.handle_before_session('你好') is None
    assert usage.snapshot()['local_handled_count'] == before


@pytest.mark.parametrize('minutes', range(1, 61))
def test_timer_range(minutes):
    assert match(f'計時{minutes}分鐘').value == minutes


@pytest.mark.asyncio
async def test_local_speech_bridge_hook(monkeypatch):
    from stackchan_mcp.gemini_voice_proxy import GeminiVoiceProxy
    from stackchan_mcp import child_profile
    monkeypatch.setattr(child_profile, 'enabled', lambda: True)
    # The hook must not call a session or turn constructor.
    proxy = object.__new__(GeminiVoiceProxy)
    proxy._edge_tts = object()
    class Handler:
        handle_before_session = AsyncMock(return_value={'handled': True})
    proxy._local_commands = Handler()
    assert await proxy.handle_local_text_before_session('你好') == {'handled': True}
    proxy._local_commands.handle_before_session.assert_awaited_once_with('你好')


@pytest.mark.asyncio
@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
async def test_game_voice_exit_is_local(tmp_path, pending, enabled):
    settings = ParentSettings(tmp_path/'settings')
    settings.set('local_commands_enabled', enabled, source='parent')
    forms = XiaoyaForms(tmp_path/'forms')
    forms.send_device = AsyncMock()
    games = XiaoyaGames(tmp_path/'games', pin_source=lambda: 'G3')
    usage = UsageStats(tmp_path/'usage')
    speak = AsyncMock()
    local = LocalCommands(settings=settings, forms=forms, games=games, usage=usage, speak=speak)
    assert await local.handle_before_session('唔玩啦') is None
    games.on_level_up(1, 2)
    if not pending:
        games.start_pending()
    assert await local.handle_before_session('我唔玩啦') is None
    assert await local.handle_before_session(' 唔 玩 啦！') == {'intent': 'game_exit', 'handled': True}
    forms.send_device.assert_awaited_once_with({
        'type': 'xiaoya', 'cmd': 'minigame', 'action': 'exit',
        'reason': 'decline' if pending else 'quit',
    })
    assert not games.active and not games.has_pending()
    assert usage.snapshot()['local_handled_count'] == 1
    speak.assert_not_awaited()
