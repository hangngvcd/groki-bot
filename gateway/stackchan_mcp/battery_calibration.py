"""Parent-only discharge calibration. SOC is assumed linear in elapsed time.

Use a full-to-shutdown discharge with USB unplugged at the start and a constant
load. This estimates a curve, not measured capacity or charging state.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import os
from pathlib import Path
from statistics import median


def validate_curve(raw, *, min_span=0):
    if (not isinstance(raw, list) or len(raw) != 6 or
            any(type(x) is not int or not 1000 <= x <= 3500 for x in raw) or
            any(a >= b for a, b in zip(raw, raw[1:]))):
        raise ValueError("curve: need six strictly increasing integers 1000..3500")
    if raw[-1] - raw[0] < min_span:
        raise ValueError(f"curve: span must be >= {min_span} counts")
    return raw


def fit(samples):
    """Pure fit of uptime_s/raw mappings; median windows suppress isolated noise.

    Refuse non-monotonic fitted knots rather than silently repair bad runs.
    """
    if len(samples) < 60:
        raise ValueError("calibration needs >= 60 samples")
    try:
        times = [float(s["uptime_s"]) for s in samples]
        values = [float(s["raw"]) for s in samples]
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid uptime_s/raw samples") from None
    if (any(not math.isfinite(x) for x in times + values) or
            any(a >= b for a, b in zip(times, times[1:])) or
            any(not 1000 <= x <= 3500 for x in values)):
        raise ValueError("need increasing uptime and plausible raw samples (1000..3500)")
    duration = times[-1] - times[0]
    if duration < 3600:
        raise ValueError("calibration needs >= 60 minutes, full to shutdown at constant load")
    smoothed = [median(values[max(0, i-2):min(len(values), i+3)]) for i in range(len(values))]
    points = []
    for pct in (0, 20, 40, 60, 80, 100):
        target = times[0] + duration * (1 - pct / 100)
        i = min(range(len(times)), key=lambda i: abs(times[i] - target))
        points.append(round(smoothed[i]))
    return validate_curve(points, min_span=150)


def fit_csv(path):
    with Path(path).expanduser().open(newline="") as stream:
        return fit(list(csv.DictReader(stream)))


class BatteryCalibration:
    def __init__(self, state_dir=None):
        self.directory = Path(state_dir or os.getenv("STACKCHAN_STATE_DIR", "~/.stackchan")).expanduser() / "battery_cal"
        self.path = None
        self.active = False
        self.interval = 30

    async def start(self, sender, interval=30):
        if type(interval) is not int or not 5 <= interval <= 600:
            raise ValueError("interval_s: expected 5..600")
        if self.active:
            raise ValueError("calibration already recording; stop first")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".csv")
        with self.path.open("x", newline="") as stream:
            os.chmod(self.path, 0o600)
            csv.writer(stream).writerow(("t_wall", "uptime_s", "raw", "mv", "pct"))
        self.interval = interval
        self.active = True
        try:
            if not await sender({"type": "xiaoya", "cmd": "battery_cal", "on": True, "interval_s": interval}):
                raise ValueError("device not connected")
        except BaseException:
            self.active = False
            self.path.unlink(missing_ok=True)
            self.path = None
            raise
        return str(self.path)

    async def stop(self, sender):
        self.active = False
        await sender({"type": "xiaoya", "cmd": "battery_cal", "on": False, "interval_s": self.interval})
        return str(self.path) if self.path else None

    def record(self, event):
        if not self.active or event.get("event") != "battery_raw":
            return
        keys = ("uptime_s", "raw", "mv", "pct")
        if any(type(event.get(k)) is not int for k in keys):
            return
        with self.path.open("a", newline="") as stream:
            csv.writer(stream).writerow((dt.datetime.now(dt.timezone.utc).isoformat(), *(event[k] for k in keys)))

    def csv_path(self, path=None):
        if path:
            return Path(path).expanduser()
        paths = sorted(self.directory.glob("*.csv"))
        if not paths:
            raise ValueError("no calibration CSV; start a discharge run first")
        return paths[-1]


_calibration = None


def get_battery_calibration():
    global _calibration
    if _calibration is None:
        _calibration = BatteryCalibration()
    return _calibration


def register_pushers(settings, forms):
    async def curve(raw):
        return await forms.send_device({"type": "xiaoya", "cmd": "battery_curve", "raw": raw})

    async def threshold(pct):
        return await forms.send_device({"type": "xiaoya", "cmd": "battery_threshold", "pct": pct})

    settings.register_pusher("battery_curve_raw", curve)
    settings.register_pusher("battery_low_threshold_pct", threshold)
