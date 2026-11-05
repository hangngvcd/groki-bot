"""Local daily counters only; no conversation or image data."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import tempfile
import time as clock_time
from zoneinfo import ZoneInfo

HK = ZoneInfo("Asia/Hong_Kong")
COUNTERS = (
    "connected_seconds",
    "connect_count",
    "disconnect_count",
    "wake_count",
    "wake_word_count",
    "button_listen_count",
    "conversation_count",
    "voice_session_seconds",
    "local_handled_count",
    "homework_photos_sent",
    "homework_photos_failed",
)


class UsageStats:
    def __init__(self, directory=None, clock=clock_time.time):
        self.directory = Path(directory or os.getenv("STACKCHAN_USAGE_DIR", "~/.stackchan/usage")).expanduser()
        self.clock = clock
        self.connected = self.voice = self.window = self.conversed = False
        self.last = self.now()
        self.records = {}
        self._record(self.last)
        self.records[self.last.date().isoformat()]["complete"] = False

    def now(self):
        return datetime.fromtimestamp(self.clock(), HK)

    def _record(self, now):
        day = now.date().isoformat()
        if day not in self.records:
            path = self.directory / (day + ".json")
            try:
                record = json.loads(path.read_text())
                if set(record) != set(self._empty(now)):
                    raise ValueError("Invalid usage schema")
            except (OSError, ValueError):
                record = self._empty(now)
            self.records[day] = record
        return self.records[day]

    def _empty(self, now):
        start = datetime.combine(now.date(), time(), HK)
        return dict(
            date=now.date().isoformat(),
            timezone="Asia/Hong_Kong",
            day_start=start.isoformat(),
            generated_at=now.isoformat(),
            counting_since=now.isoformat(),
            complete=now == start,
            connected_now=False,
            **dict.fromkeys(COUNTERS, 0),
            last_activity_at=None,
        )

    def advance(self):
        now = self.now()
        cursor = self.last
        while cursor < now:
            boundary = datetime.combine(cursor.date() + timedelta(days=1), time(), HK)
            end = min(now, boundary)
            record = self._record(cursor)
            seconds = (end - cursor).total_seconds()
            record["connected_seconds"] += seconds if self.connected else 0
            record["voice_session_seconds"] += seconds if self.voice else 0
            record["generated_at"] = end.isoformat()
            record["connected_now"] = self.connected if end < boundary else False
            cursor = end
            if cursor == boundary:
                self._record(cursor)
        self.last = now
        return self._record(now)

    def activity(self):
        self.advance()["last_activity_at"] = self.now().isoformat()

    def device(self, connected):
        record = self.advance()
        if connected != self.connected:
            record["connect_count" if connected else "disconnect_count"] += 1
        self.connected = connected
        if not connected:
            self.voice = self.window = False
            self.flush()

    def voice_session(self, active):
        self.advance()
        self.voice = active

    def wake(self, button=False):
        record = self.advance()
        record["wake_count"] += 1
        record["button_listen_count" if button else "wake_word_count"] += 1
        self.window, self.conversed = True, False
        self.activity()

    def audio(self):
        record = self.advance()
        if self.window and not self.conversed:
            record["conversation_count"] += 1
            self.conversed = True
        self.activity()

    def local_handled(self):
        self.advance()["local_handled_count"] += 1
        self.flush()

    def photo(self, sent):
        self.advance()[
            "homework_photos_sent" if sent else "homework_photos_failed"
        ] += 1

    def snapshot(self, day=None):
        self.advance()
        today = self.now().date()
        try:
            selected = (
                today if day is None else datetime.strptime(day, "%Y-%m-%d").date()
            )
            if day is not None and selected.isoformat() != day:
                raise ValueError()
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD") from None
        if not today - timedelta(days=13) <= selected <= today:
            raise ValueError("date outside retained 14 days")
        key = selected.isoformat()
        if key not in self.records:
            try:
                self.records[key] = json.loads(
                    (self.directory / (key + ".json")).read_text()
                )
            except FileNotFoundError:
                return None
        result = dict(self.records[key])
        result["generated_at"] = self.now().isoformat()
        result["connected_now"] = self.connected if selected == today else False
        for name in ("connected_seconds", "voice_session_seconds"):
            result[name] = int(result[name])
        return result

    def flush(self):
        record = self.advance()
        record["connected_now"] = self.connected
        record["generated_at"] = self.now().isoformat()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        cutoff = (self.now().date() - timedelta(days=13)).isoformat()
        for day, record in list(self.records.items()):
            if day < cutoff:
                del self.records[day]
                continue
            fd, name = tempfile.mkstemp(prefix=".usage-", dir=self.directory)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(record, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.directory / (day + ".json"))
            finally:
                Path(name).unlink(missing_ok=True)
        for path in self.directory.glob("????-??-??.json"):
            if path.stem < cutoff:
                path.unlink()

    async def run(self):
        try:
            while True:
                await asyncio.sleep(60)
                self.flush()
        finally:
            self.flush()


_usage = None


def get_usage_stats():
    global _usage
    if _usage is None:
        _usage = UsageStats()
    return _usage


def format_usage(record, day):
    if record is None:
        return f"{day}（香港時間）未有用量紀錄"

    def duration(seconds):
        minutes = seconds // 60
        return (
            f"{minutes // 60} 小時 {minutes % 60} 分"
            if minutes >= 60
            else f"{minutes} 分"
        )

    r = record
    lines = [
        f"{r['date']}（香港時間）小芽用量：",
        f"連線時間：{duration(r['connected_seconds'])}（連線 {r['connect_count']} 次、斷線 {r['disconnect_count']} 次，而家{'已連線' if r['connected_now'] else '未連線'}）",
        f"叫醒聆聽：{r['wake_count']} 次（叫「小白」{r['wake_word_count']} 次、撳掣 {r['button_listen_count']} 次）",
        f"對話：{r['conversation_count']} 次；語音時間：{duration(r['voice_session_seconds'])}",
        f"本地處理：{r['local_handled_count']} 次",
        f"功課相：送咗 {r['homework_photos_sent']} 張，送唔到 {r['homework_photos_failed']} 張",
        f"最後活動：{r['last_activity_at'][11:16] if r['last_activity_at'] else '未有'}",
    ]
    if not r["complete"]:
        lines.append(f"（{r['counting_since'][11:16]} 先開始計，之前 gateway 冇行）")
    return "\n".join(lines)
