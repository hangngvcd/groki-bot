import asyncio

import pytest

from stackchan_mcp.xiaoya_forms import XiaoyaForms, SESSION_IDLE_SECONDS


@pytest.mark.asyncio
async def test_session_start_timeout_and_reconnect(tmp_path):
    now = [0.0]
    forms = XiaoyaForms(tmp_path / 'forms.json', clock=lambda: now[0])
    messages = []

    async def send(message):
        messages.append(message)

    forms.register_sender(send)
    forms.start_homework_session()
    await asyncio.sleep(0)
    assert {'type': 'xiaoya', 'cmd': 'session', 'active': True} in messages
    now[0] = SESSION_IDLE_SECONDS - 1
    forms.check_session_timeout()
    assert forms.session_active
    forms.note_interaction()
    now[0] += SESSION_IDLE_SECONDS - 1
    forms.check_session_timeout()
    assert forms.session_active
    forms.unregister_sender()
    messages.clear()
    forms.register_sender(send, push=False)
    forms.note_device_event({'event': 'form_state'})
    await asyncio.sleep(0)
    assert forms.session_message() in messages
    now[0] += 1
    forms.check_session_timeout()
    await asyncio.sleep(0)
    assert not forms.session_active
    assert messages[-1] == {'type': 'xiaoya', 'cmd': 'session', 'active': False}
    forms.start_homework_session()
    forms.end_homework_session()
    assert not forms.session_active
    forms.unregister_sender()


@pytest.mark.asyncio
async def test_voice_list_and_shougong(tmp_path, monkeypatch):
    from stackchan_mcp import xiaoya_forms
    from stackchan_mcp.voice_tools import VoiceToolExecutor

    forms = XiaoyaForms(tmp_path / 'voice-forms.json')
    monkeypatch.setattr(xiaoya_forms, '_forms', forms)
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    executor = VoiceToolExecutor()
    try:
        result = await executor.execute('homework_list', {})
        assert result['ok'] and forms.session_active
        result = await executor.execute('end_homework_session', {})
        assert result['ok'] and not forms.session_active
    finally:
        await executor.close()
