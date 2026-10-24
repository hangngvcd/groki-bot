"""Child-profile debug HTTP privacy boundary."""

import json
from unittest.mock import MagicMock, Mock

import pytest
from aiohttp.test_utils import make_mocked_request

from stackchan_mcp.capture_server import create_capture_app
from stackchan_mcp.debug_status import DebugStatus


async def debug_request(app, path, *, host="localhost", auth="", payload=None):
    """Resolve and exercise the real route and middleware without opening sockets."""
    from stackchan_mcp.capture_server import child_debug_guard

    transport = Mock()
    transport.get_extra_info.return_value = ("127.0.0.1", 1234)
    request = make_mocked_request(
        "POST" if payload is not None else "GET",
        path,
        headers={"Host": host, "Authorization": auth},
        transport=transport,
        app=app,
    )
    if payload is not None:

        async def read_json():
            return payload

        request.json = read_json
    route = await app.router.resolve(request)
    assert child_debug_guard in app.middlewares
    return await child_debug_guard(request, route.handler)


def test_child_transcripts_are_not_retained(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    status = DebugStatus()
    status.record_transcript("我今日功課係…")
    assert not status.recent_transcripts
    status.record_tool_call("end_conversation", False, error="secret text")
    recent = status.snapshot()["recent"]
    assert recent["transcripts"] == []
    assert recent["redacted"] is True
    assert "secret text" not in json.dumps(recent)
    assert set(recent["tool_calls"][0]) == {"name", "ok", "at"}


@pytest.mark.parametrize(
    "enabled,host,expected",
    [
        ("1", "0.0.0.0", "127.0.0.1"),
        ("0", "192.168.1.5", "192.168.1.5"),
        ("0", None, "0.0.0.0"),
    ],
)
def test_bind_host(monkeypatch, enabled, host, expected):
    from stackchan_mcp.capture_server import capture_bind_host

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", enabled)
    monkeypatch.delenv("HOST", raising=False)
    if host is not None:
        monkeypatch.setenv("HOST", host)
    assert capture_bind_host() == expected
    assert capture_bind_host("0.0.0.0") == (
        "127.0.0.1" if enabled == "1" else "0.0.0.0"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/debug/status", "/debug/panel", "/debug/inject-text"]
)
@pytest.mark.parametrize(
    "remote,host",
    [
        ("192.168.1.23", "localhost"),
        (None, "localhost"),
        ("127.0.0.1", "evil.example"),
        ("127.0.0.1", "192.168.1.5:8766"),
        ("127.0.0.1", "localhost.evil.example"),
        ("127.0.0.1", "localhost:8766@evil.example"),
    ],
)
async def test_child_guard_rejects_remote_or_host(monkeypatch, path, remote, host):
    from stackchan_mcp.capture_server import child_debug_guard

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    transport = Mock()
    transport.get_extra_info.side_effect = lambda key, default=None: (
        (remote, 1234) if key == "peername" and remote else default
    )
    request = make_mocked_request(
        "GET", path, headers={"Host": host}, transport=transport
    )

    async def unreachable(_request):
        pytest.fail("Rejected request reached handler")

    response = await child_debug_guard(request, unreachable)
    assert response.status == 403
    assert json.loads(response.text) == {"error": "Forbidden"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8766", "127.0.0.1", "[::1]:8766"]
)
@pytest.mark.parametrize(
    "token,auth,expected",
    [
        ("", "", 200),
        ("secret", "", 401),
        ("secret", "Bearer wrong", 401),
        ("secret", "Bearer secret", 200),
    ],
)
async def test_child_status_auth_and_redaction(
    monkeypatch, host, token, auth, expected
):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "0")
    status = DebugStatus()
    status.record_transcript("old conversation")
    status.record_tool_call("end_conversation", False, error="secret text")
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setenv("STACKCHAN_TOKEN", token)
    status.record_transcript("我今日功課係…")
    response = await debug_request(
        create_capture_app(debug_status=status), "/debug/status", host=host, auth=auth
    )
    assert response.status == expected
    if expected == 200:
        text = response.text
        assert all(
            secret not in text
            for secret in ["old conversation", "我今日功課係…", "secret text"]
        )
        recent = json.loads(text)["recent"]
        assert recent["transcripts"] == []
        assert recent["redacted"] is True
        assert set(recent["tool_calls"][0]) == {"name", "ok", "at"}


@pytest.mark.asyncio
async def test_panel_and_injection_token_policy(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.setenv("STACKCHAN_TOKEN", "secret")
    app = create_capture_app()
    response = await debug_request(app, "/debug/panel")
    assert response.status == 200
    html = response.text
    assert "location.hash" in html
    assert 'Authorization: "Bearer " + token' in html
    assert "?token=" not in html
    for auth, expected in [("", 401), ("Bearer wrong", 401), ("Bearer secret", 200)]:
        response = await debug_request(
            app, "/debug/inject-text", payload={"text": "hello"}, auth=auth
        )
        assert response.status == expected
    monkeypatch.delenv("STACKCHAN_TOKEN")
    response = await debug_request(
        create_capture_app(), "/debug/inject-text", payload={"text": "hello"}
    )
    assert response.status == 403


@pytest.mark.asyncio
async def test_child_off_status_is_open_and_unredacted(monkeypatch):
    from stackchan_mcp.capture_server import child_debug_guard, handle_debug_status

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "0")
    monkeypatch.setenv("STACKCHAN_TOKEN", "secret")
    status = DebugStatus()
    status.record_transcript("conversation")
    status.record_tool_call("tool", False, error="error text")
    transport = Mock()
    transport.get_extra_info.return_value = ("192.168.1.23", 1234)
    request = make_mocked_request(
        "GET",
        "/debug/status",
        headers={"Host": "evil.example"},
        transport=transport,
        app=create_capture_app(debug_status=status),
    )
    response = await child_debug_guard(request, handle_debug_status)
    assert response.status == 200
    recent = json.loads(response.text)["recent"]
    assert recent["transcripts"][0]["text"] == "conversation"
    assert recent["tool_calls"][0]["error"] == "error text"
    assert "redacted" not in recent


@pytest.mark.asyncio
@pytest.mark.parametrize("remote,host", [("::1", "[::1]"), ("127.42.0.9", "localhost")])
async def test_loopback_guard(monkeypatch, remote, host):
    from stackchan_mcp.capture_server import child_debug_guard, handle_debug_status

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    monkeypatch.delenv("STACKCHAN_TOKEN", raising=False)
    transport = Mock()
    transport.get_extra_info.return_value = (remote, 1234)
    request = make_mocked_request(
        "GET",
        "/debug/status",
        headers={"Host": host},
        transport=transport,
        app=create_capture_app(),
    )
    assert (await child_debug_guard(request, handle_debug_status)).status == 200


def test_e2e_status_bearer(monkeypatch):
    from stackchan_mcp.e2e_check import E2EChecker

    monkeypatch.setenv("STACKCHAN_TOKEN", "secret")
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = b"{}"
    monkeypatch.setattr("stackchan_mcp.e2e_check._no_proxy_opener", lambda: opener)
    assert E2EChecker().fetch_status() == {}
    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer secret"
    assert "secret" not in request.full_url


def test_child_diagnostics_describe_debug_privacy(monkeypatch):
    from stackchan_mcp.child_profile import diagnostics

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    assert "debug status: localhost-only; conversation text redacted" in diagnostics()
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "0")
    assert diagnostics() == []
