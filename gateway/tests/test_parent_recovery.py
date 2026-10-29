import json
import pytest
from aiohttp.test_utils import make_mocked_request
from stackchan_mcp.parent_recovery import RecoveryStore
from stackchan_mcp.parent_api import create_parent_app
from stackchan_mcp.parent_settings import ParentSettings
from stackchan_mcp.xiaoya_forms import XiaoyaForms


@pytest.mark.asyncio
async def test_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('STACKCHAN_STATE_DIR', str(tmp_path))
    store = RecoveryStore()
    image = tmp_path/'homework.jpg'
    image.write_bytes(b'jpeg')
    store.record(item='math', path=image, notified=False)
    app = create_parent_app('parent', settings=ParentSettings(tmp_path/'settings'), forms=XiaoyaForms(tmp_path/'forms'))
    async def call(method, path, token='parent'):
        req = make_mocked_request(method, path, headers={'Host':'localhost','Authorization':'Bearer '+token})
        # Remote is provided by transport in production.
        req._cache['remote'] = '127.0.0.1'
        match = await app.router.resolve(req)
        req._match_info = match
        return await app.middlewares[0](req, match.handler)
    assert (await call('GET','/parent/v1/photos','wrong')).status == 401
    response = await call('GET','/parent/v1/photos')
    assert json.loads(response.body)['photos'][0]['available']
    assert (await call('GET','/parent/v1/photos/1/path')).status == 200
    assert (await call('POST','/parent/v1/photos/1/done')).status == 200
    assert store.photo('1')['done']
    with pytest.raises(Exception) as error:
        await call('GET','/parent/v1/photos/../../etc/passwd/path')
    assert error.value.status == 404
    assert len(json.loads((await call('GET','/parent/v1/events')).body)['events']) == 1
    assert store.events(1) == []
    assert (await call('GET','/parent/v1/events?after=no')).status == 400
