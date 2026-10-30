"""Pytest configuration."""

import os

import pytest

# Tests spin up loopback WebSocket servers. websockets 14+ honors proxy env
# vars (HTTP_PROXY etc.) for ws:// clients, so a system proxy would hijack
# test connections to 127.0.0.1 and they'd time out. Bypass proxies entirely.
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_var, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "defaults: run with the shipped default environment (no opt-in tool flags)",
    )


# Use asyncio mode for all async tests
def pytest_collection_modifyitems(config, items):
    """Auto-mark all async tests."""
    for item in items:
        if item.get_closest_marker("asyncio") is None:
            if asyncio_test(item):
                item.add_marker(pytest.mark.asyncio)


def asyncio_test(item):
    """Check if test is async."""
    return hasattr(item, "function") and hasattr(item.function, "__wrapped__")


@pytest.fixture(autouse=True)
def _no_real_face_tracker(monkeypatch):
    """Gateway tests never launch the real Mac camera face tracker."""
    monkeypatch.setenv("STACKCHAN_FACE_TRACKER_AUTOSTART", "0")
    monkeypatch.delenv("STACKCHAN_FACE_TRACKER_CAMERA", raising=False)


@pytest.fixture(autouse=True)
def _isolated_xiaoya_forms(monkeypatch, tmp_path):
    """鈴鈴 form state never touches the real ~/.stackchan during tests."""
    from stackchan_mcp.xiaoya_forms import reset_xiaoya_forms

    monkeypatch.setenv("STACKCHAN_XIAOYA_FORMS_PATH", str(tmp_path / "xiaoya_forms.json"))
    reset_xiaoya_forms()
    yield
    reset_xiaoya_forms()


@pytest.fixture(autouse=True)
def _reset_debug_status():
    """Keep the process-wide debug snapshot from leaking across tests."""
    from stackchan_mcp.debug_status import reset_debug_status

    reset_debug_status()
    yield
    reset_debug_status()


@pytest.fixture(autouse=True)
def _isolated_parent_settings(monkeypatch, tmp_path):
    """Parent settings (e.g. mandarin) never read the real ~/.stackchan in tests."""
    from stackchan_mcp.parent_settings import reset_parent_settings

    monkeypatch.setenv(
        "STACKCHAN_PARENT_SETTINGS_PATH", str(tmp_path / "parent_settings_isolated.json")
    )
    reset_parent_settings()
    yield
    reset_parent_settings()


@pytest.fixture(autouse=True)
def _isolated_kws_miss_tip(monkeypatch, tmp_path):
    """Daily wake tests never touch real state or message the parent helper."""
    from stackchan_mcp import kws_miss_tip

    monkeypatch.setenv("STACKCHAN_KWS_MISS_TIP_PATH", str(tmp_path / "kws_tip.json"))
    monkeypatch.setattr(kws_miss_tip, "_send", lambda *args: None)
    kws_miss_tip.reset_kws_miss_tip()
    yield
    kws_miss_tip.reset_kws_miss_tip()


@pytest.fixture(autouse=True)
def _reset_voice_language_mode():
    """46c: the live language mode is module state; keep tests independent."""
    from stackchan_mcp import gbot_dialogue

    gbot_dialogue.set_active_language_mode("yue")
    yield
    gbot_dialogue.set_active_language_mode("yue")
