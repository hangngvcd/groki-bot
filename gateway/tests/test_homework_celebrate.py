import asyncio

import pytest

from stackchan_mcp.xiaoya_forms import XiaoyaForms


@pytest.fixture(autouse=True)
def games(tmp_path, monkeypatch):
    from stackchan_mcp import xiaoya_games
    monkeypatch.setattr(xiaoya_games, "_games", xiaoya_games.XiaoyaGames(tmp_path / "games.json"))


def test_item_all_once_redo_and_hk_day_persist(tmp_path):
    day = ['2026-10-28']
    path = tmp_path / 'forms.json'
    forms = XiaoyaForms(path, today=lambda: day[0])
    forms.add_items(['中文', '數學'], source='test')
    first = forms.mark_done(1, source='test')
    assert first['device_messages'][0]['level'] == 'item'
    assert forms.mark_done(1, source='test')['device_messages'] == []
    last = forms.mark_done(2, source='test')
    assert last['device_messages'][0]['level'] == 'all'
    forms = XiaoyaForms(path, today=lambda: day[0])
    forms.redo_items([2], source='parent')
    assert forms.mark_done(2, source='test')['device_messages'][0]['level'] == 'item'
    day[0] = '2026-10-29'
    forms.add_items(['英文'], source='test')
    assert forms.mark_done(1, source='test')['device_messages'][0]['level'] == 'all'
    for line in (first['spoken'], last['spoken']):
        assert not any(word in line for word in ('叻', '聰明', '天才'))


@pytest.mark.asyncio
async def test_celebrate_before_session_end(tmp_path):
    forms = XiaoyaForms(tmp_path / 'forms.json')
    messages = []

    async def send(message):
        messages.append(message)

    forms.register_sender(send)
    forms.add_items(['中文'], source='test')
    forms.start_homework_session()
    await asyncio.sleep(0)
    messages.clear()
    result = forms.mark_done(1, source='test')
    await asyncio.sleep(0)
    completion = [m for m in messages if m['cmd'] in {'celebrate', 'session'}]
    assert completion == result['device_messages'] == [
        {'type': 'xiaoya', 'cmd': 'celebrate', 'level': 'all'},
        {'type': 'xiaoya', 'cmd': 'session', 'active': False},
    ]
    assert not forms.session_active
    forms.unregister_sender()
