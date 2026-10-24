# SPDX-License-Identifier: MIT
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash"), reason="requires bash"
)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/xiaoya_services.sh"
TOKEN = "private-device-token-123456789012"


@pytest.fixture
def service(tmp_path):
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    config = tmp_path / "services.env"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    record = tmp_path / "calls"
    for name in ("launchctl", "uv", "node"):
        path = binaries / name
        path.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALL_RECORD"\n')
        path.chmod(0o755)
    config.write_text(
        "STACKCHAN_GBOT_VOICE_BOT_ID=abcd-private-agent\n"
        "STACKCHAN_TOOL_BOT_ID=abcd-private-agent\n"
        'STACKCHAN_TOOL_BOT=agent&<>"小白\n'
        f"STACKCHAN_TOKEN={TOKEN}\n"
        "STACKCHAN_USB_DISABLE=0\nSTACKCHAN_USB_TRANSPORT=serial\n"
        "STACKCHAN_CHILD_PROFILE=0\nSTACKCHAN_PARENT_TOKEN=ignored\n"
        f"UV_BIN={binaries / 'uv'}\nNODE_BIN={binaries / 'node'}\n"
    )
    config.chmod(0o600)
    env = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "CALL_RECORD": str(record),
    }

    def run(*args):
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--config",
                str(config),
                "--home",
                str(home),
                "--launch-agents-dir",
                str(agents),
                *args,
            ],
            env=env,
            capture_output=True,
            text=True,
        )

    return run, home, agents, config, record


def test_dry_install(service):
    run, home, _, _, record = service
    result = run("--dry-run", "install")
    assert result.returncode == 0, result.stderr
    assert not home.exists()
    assert not record.exists()
    assert TOKEN not in result.stdout + result.stderr


def test_install_and_uninstall(service):
    run, home, agents, _, record = service
    result = run("install")
    assert result.returncode == 0, result.stderr
    loaded = {}
    for name in ("gateway", "login-helper"):
        path = agents / f"local.xiaoya.{name}.plist"
        assert path.stat().st_mode & 0o777 == 0o600
        loaded[name] = plistlib.loads(path.read_bytes())
        assert loaded[name]["KeepAlive"] is True
        assert loaded[name]["RunAtLoad"] is True
        assert loaded[name]["ThrottleInterval"] == 10
    gateway = loaded["gateway"]["EnvironmentVariables"]
    assert gateway["STACKCHAN_TOKEN"] == TOKEN
    for key, value in {
        "STACKCHAN_USB_DISABLE": "1",
        "STACKCHAN_CHILD_PROFILE": "1",
        "STACKCHAN_VOICE_BACKEND": "gbot",
        "STACKCHAN_WAKE_PHRASE": "小白",
        "STACKCHAN_PARENT_API": "1",
        # Privacy: never pass-through, even without a wake word model.
        "STACKCHAN_BUTTON_ONLY_LISTEN": "1",
        "STACKCHAN_WAKE_WORD": "1",
    }.items():
        assert gateway[key] == value
    assert "STACKCHAN_USB_TRANSPORT" not in gateway
    assert "STACKCHAN_PARENT_TOKEN" not in gateway
    helper = loaded["login-helper"]["EnvironmentVariables"]
    assert set(helper) == {
        "STACKCHAN_GBOT_VOICE_BOT_ID",
        "STACKCHAN_TOOL_BOT",
        "STACKCHAN_TOOL_BOT_ID",
        "PATH",
        "HOME",
    }
    assert helper["STACKCHAN_TOOL_BOT"] == 'agent&<>"小白'
    wrapper = home / ".stackchan/bin/stackchan-parent"
    assert wrapper.stat().st_mode & 0o777 == 0o755
    assert (home / "Library/Logs/xiaoya").stat().st_mode & 0o777 == 0o700
    assert "bootstrap" in record.read_text()
    result = run("uninstall")
    assert result.returncode == 0, result.stderr
    assert not wrapper.exists()
    assert not list(agents.glob("*.plist"))
    assert "bootout" in record.read_text()


@pytest.mark.parametrize(
    "change", ["mismatch", "permissions", "placeholder", "invalid"]
)
def test_reject_config(service, change):
    run, _, _, config, _ = service
    if change == "permissions":
        config.chmod(0o644)
    else:
        content = config.read_text()
        if change == "mismatch":
            content = content.replace(
                "STACKCHAN_TOOL_BOT_ID=abcd-private-agent",
                "STACKCHAN_TOOL_BOT_ID=other",
            )
        elif change == "placeholder":
            content = content.replace("abcd-private-agent", "YOUR_AGENT_ID")
        else:
            content += "echo unsafe\n"
        config.write_text(content)
    result = run("install")
    assert result.returncode != 0
    assert TOKEN not in result.stdout + result.stderr


def test_check_masks(service):
    run, home, _, _, record = service
    result = run("check")
    assert result.returncode == 0, result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert f"STACKCHAN_TOKEN=****({len(TOKEN)} chars)" in result.stdout
    assert "abcd-private-agent" not in result.stdout
    assert not home.exists()
    assert not record.exists()


def test_status_filters_launchctl_environment(service):
    run, _, _, _, record = service
    assert run("install").returncode == 0
    launchctl = record.parent / "bin/launchctl"
    launchctl.write_text(
        "#!/bin/bash\n"
        f'printf "    STACKCHAN_TOKEN => {TOKEN}\\n"\n'
        'printf "    state = running\\n    pid = 123\\n"\n'
    )
    result = run("status")
    assert result.returncode == 0, result.stderr
    assert "state = running" in result.stdout
    assert TOKEN not in result.stdout + result.stderr


def test_button_only_fallback_keeps_privacy_gate(service):
    run, _, agents, config, _ = service
    config.write_text(config.read_text() + "STACKCHAN_WAKE_WORD=0\nSTACKCHAN_BUTTON_ONLY_LISTEN=0\n")
    result = run("install")
    assert result.returncode == 0, result.stderr
    gateway = plistlib.loads((agents / "local.xiaoya.gateway.plist").read_bytes())
    env = gateway["EnvironmentVariables"]
    assert env["STACKCHAN_WAKE_WORD"] == "0"
    assert env["STACKCHAN_BUTTON_ONLY_LISTEN"] == "1"
