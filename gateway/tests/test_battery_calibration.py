import asyncio
import csv
import random

import pytest

from stackchan_mcp.battery_calibration import BatteryCalibration, fit, fit_csv, register_pushers
from stackchan_mcp.parent_settings import ParentSettings
from stackchan_mcp.xiaoya_forms import XiaoyaForms


def run_samples():
    return [{"uptime_s": i * 60, "raw": 3000 - i * 10} for i in range(121)]


def test_fit_and_noise():
    assert fit(run_samples()) == [1810, 2040, 2280, 2520, 2760, 2990]
    rng = random.Random(44)
    samples = run_samples()
    for s in samples:
        s["raw"] += rng.randint(-12, 12)
    assert all(abs(a-b) < 30 for a, b in zip(fit(samples), [1800,2040,2280,2520,2760,3000]))


@pytest.mark.parametrize("samples,match", [
    (run_samples()[:30], "60 samples"),
    ([{"uptime_s": i, "raw": 3000-i} for i in range(100)], "60 minutes"),
    ([{"uptime_s": i*60, "raw": 1800+i*10} for i in range(121)], "strictly increasing"),
    ([{"uptime_s": i*60, "raw": 2500 - i//2} for i in range(121)], "span"),
    ([{"uptime_s": i*60, "raw": 1800 + abs(i-60)*10} for i in range(121)], "strictly increasing"),
])
def test_refuses_bad_runs(samples, match):
    with pytest.raises(ValueError, match=match):
        fit(samples)


@pytest.mark.asyncio
async def test_record_only_raw(tmp_path):
    sent = []
    async def sender(message):
        sent.append(message)
        return True
    cal = BatteryCalibration(tmp_path)
    path = await cal.start(sender, 30)
    cal.record({"event": "battery", "raw": 2400})
    for s in run_samples():
        cal.record({"event": "battery_raw", **s, "mv": -1, "pct": -1, "secret": "never stored"})
    await cal.stop(sender)
    cal.record({"event": "battery_raw", "uptime_s": 9000, "raw": 2000, "mv": -1, "pct": -1})
    with open(path) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 121
    assert set(rows[0]) == {"t_wall", "uptime_s", "raw", "mv", "pct"}
    assert fit_csv(path) == fit(run_samples())
    assert sent == [{"type":"xiaoya", "cmd":"battery_cal", "on":on, "interval_s":30} for on in (True,False)]


@pytest.mark.asyncio
async def test_apply_and_reconnect(tmp_path, monkeypatch):
    from stackchan_mcp import parent_settings
    settings = ParentSettings(tmp_path / "settings.json")
    monkeypatch.setattr(parent_settings, "_settings", settings)
    forms = XiaoyaForms(tmp_path / "forms.json")
    register_pushers(settings, forms)
    sent = []
    async def sender(message):
        sent.append(message)
    forms.register_sender(sender, push=False)
    for _ in range(2):
        forms.note_device_event({"event": "form_state", "form": 1})
        await asyncio.gather(*tuple(forms._tasks))
    assert sent.count({"type":"xiaoya", "cmd":"battery_curve", "raw":None}) == 2
    assert sent.count({"type":"xiaoya", "cmd":"battery_threshold", "pct":20}) == 2
    settings.update({"battery_curve_raw": [1800,2000,2200,2400,2600,3000], "battery_low_threshold_pct":25}, source="test-parent")
    await asyncio.gather(*tuple(settings._tasks))
    assert {"type":"xiaoya", "cmd":"battery_threshold", "pct":25} in sent
    assert sent[-2]["raw"] == [1800,2000,2200,2400,2600,3000]
    forms.unregister_sender()


@pytest.mark.parametrize("values", [{"battery_curve_raw": [1000]*6}, {"battery_curve_raw": [True]*6}, {"battery_low_threshold_pct": 51}])
def test_settings_refuse(values, tmp_path):
    with pytest.raises(ValueError):
        ParentSettings(tmp_path / "settings.json").update(values, source="parent")


@pytest.mark.asyncio
async def test_start_disconnected(tmp_path):
    async def sender(message):
        return False
    cal = BatteryCalibration(tmp_path)
    with pytest.raises(ValueError, match="not connected"):
        await cal.start(sender)
    assert not cal.active
    assert not list(cal.directory.glob("*.csv"))


def test_cli_battery_commands(monkeypatch, capsys):
    from stackchan_mcp import parent_cli
    calls = []
    def call(self, endpoint, values=None):
        calls.append((endpoint, values))
        return {"ok": True}
    monkeypatch.setattr(parent_cli.ParentClient, "call", call)
    assert parent_cli.main(["battery-cal", "start", "--interval", "30"]) == 0
    assert calls[-1] == ("battery-cal", {"action":"start", "interval_s":30})
    assert parent_cli.main(["battery-cal", "fit"]) == 0
    assert parent_cli.main(["battery-cal", "apply", "--raw", "1800,2000,2200,2400,2600,3000"]) == 0
    assert calls[-1][1]["raw"] == [1800,2000,2200,2400,2600,3000]
    assert parent_cli.main(["battery-cal", "apply"]) == 1
    assert parent_cli.main(["battery-cal", "reset"]) == 0
    assert parent_cli.main(["battery-threshold", "25"]) == 0
    assert calls[-1] == ("settings", {"battery_low_threshold_pct":25})


@pytest.mark.asyncio
async def test_parent_api_calibration_and_auth(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from stackchan_mcp import battery_calibration
    from stackchan_mcp.parent_api import create_parent_app
    monkeypatch.setenv("STACKCHAN_TOKEN", "child-token")
    settings = ParentSettings(tmp_path / "settings.json")
    forms = XiaoyaForms(tmp_path / "forms.json")
    monkeypatch.setattr(battery_calibration, "_calibration", BatteryCalibration(tmp_path))
    forms._sender = AsyncMock()
    forms._session_ready = True
    app = create_parent_app("parent-token", settings=settings, forms=forms)
    handler = next(route.handler for route in app.router.routes() if route.resource.canonical.endswith("/battery-cal"))
    async def request(body, token="parent-token"):
        req = SimpleNamespace(remote="127.0.0.1", headers={"Host":"127.0.0.1", "Authorization":"Bearer " + token}, json=AsyncMock(return_value=body))
        response = await app.middlewares[0](req, handler)
        return response.status, json.loads(response.text)
    assert (await request({"action":"reset"}, "child-token"))[0] == 401
    assert (await request({"action":"start", "interval_s":4}))[0] == 400
    code, payload = await request({"action":"start", "interval_s":30})
    assert code == 200
    for sample in run_samples():
        battery_calibration._calibration.record({"event":"battery_raw", **sample, "mv":-1, "pct":-1})
    assert (await request({"action":"stop"}))[0] == 200
    code, proposed = await request({"action":"fit"})
    assert code == 200
    assert settings.get()["battery_curve_raw"] is None
    assert (await request({"action":"apply", "csv":payload["csv"]}))[0] == 200
    await asyncio.gather(*tuple(settings._tasks))
    assert settings.get()["battery_curve_raw"] == proposed["proposed_curve_raw"]
    assert (await request({"action":"reset"}))[0] == 200
    await asyncio.gather(*tuple(settings._tasks))
    assert settings.get()["battery_curve_raw"] is None
    assert (await request({"action":"apply"}))[0] == 400


def test_curve_setting_does_not_alias_mutable_input(tmp_path):
    settings = ParentSettings(tmp_path / "settings.json")
    raw = [1800,2000,2200,2400,2600,3000]
    settings.set("battery_curve_raw", raw, source="parent")
    raw[0] = 1
    snapshot = settings.get()
    snapshot["battery_curve_raw"][0] = 2
    assert settings.get()["battery_curve_raw"][0] == 1800


def test_usage_dir_override(tmp_path, monkeypatch):
    from stackchan_mcp.usage_stats import UsageStats
    monkeypatch.setenv("STACKCHAN_USAGE_DIR", str(tmp_path / "usage"))
    assert UsageStats().directory == tmp_path / "usage"
