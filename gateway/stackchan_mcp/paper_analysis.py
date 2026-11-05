# SPDX-License-Identifier: MIT
"""考卷分析 v1: Mac-local paper photos, reports, weak points and daily practice.

Everything lives under ``STACKCHAN_XIAOYA_DATA_DIR`` (default ``~/xiaoya-data``)
and never leaves the Mac::

    papers/inbox/                       dad drops paper photos here
    papers/YYYY-MM-DD-<report id>/      photos of an analysed paper (90 days)
    reports/YYYY-MM-DD-<科目>-<名>.md    full report (until school-year end)
    weak_points.json                    the only file the child side reads
    practice.json                       daily practice counters (no answers)

The parent assistant writes the analysis itself (it reads the photos on the
Mac); the gateway only stores it.  Proposed weak points stay ``pending`` until
dad confirms them, and only ``active`` items ever reach 鈴鈴.  No score, mark
or photo path is stored in weak_points.json, and the child-facing view carries
only the topic (label / chars / practice type).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

HK = ZoneInfo("Asia/Hong_Kong")
PHOTO_RETENTION_DAYS = 90
PRACTICE_LOG_DAYS = 400
DAILY_LIMIT = 2
DEFAULT_PASS_TARGET = 3
MAX_WEAK_POINTS_PER_REPORT = 3
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
SUBJECTS = {"math": "數學", "chinese_vocab": "中文生字"}
PRACTICES = {"math": {"similar_question"}, "chinese_vocab": {"vocab_write", "vocab_dictation", "vocab_meaning"}}
STATUSES = {"pending", "active", "paused", "passed"}
ITEM_KEYS = {
    "id", "subject", "label", "practice", "chars", "mistake", "status", "added",
    "confirmed_at", "report_id", "seen", "pass_streak", "pass_target",
    "practiced", "passed_at",
}
PROPOSAL_KEYS = {"id", "subject", "label", "practice", "chars", "mistake", "seen", "pass_target"}
# Homework titles that make a confirmed weak point of that subject "related".
SUBJECT_KEYWORDS = {
    "math": ("數學", "math", "分數", "數"),
    "chinese_vocab": ("中文", "生字", "默書", "詞語", "字詞", "抄"),
}
# Scores never enter the weak-point list: "18/20", "18／20", "85分", "滿分", "%".
# "分數" (fractions) and "分母" are topics, so a bare 分 is allowed.
_SCORE = re.compile(r"\d+\s*[/／]\s*\d+|\d+\s*分(?![之數母子])|滿分|分數[:：]|\d+\s*%|score|mark", re.I)
_ID = re.compile(r"[\w\-]{1,48}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def data_dir(env=None):
    env = os.environ if env is None else env
    return Path(env.get("STACKCHAN_XIAOYA_DATA_DIR") or "~/xiaoya-data").expanduser()


def school_year_end(env=None):
    """MM-DD of the last school day; reports / weak points go after it."""
    env = os.environ if env is None else env
    value = env.get("STACKCHAN_SCHOOL_YEAR_END", "08-31")
    try:
        month, day = (int(part) for part in value.split("-"))
        date(2024, month, day)
    except (TypeError, ValueError):
        logger.warning("Invalid STACKCHAN_SCHOOL_YEAR_END; using 08-31")
        month, day = 8, 31
    return month, day


def year_end_after(day, end):
    month, dom = end
    candidate = date(day.year, month, min(dom, 28) if (month, dom) == (2, 29) else dom)
    return candidate if candidate >= day else date(day.year + 1, candidate.month, candidate.day)


def _private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _write(path, text):
    _private_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable %s", path.name)
        return default


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{name}: expected text up to {limit} characters")
    value = value.strip()
    if _SCORE.search(value):
        raise ValueError(f"{name}: scores / marks are not stored")
    return value


def _safe_name(value):
    cleaned = re.sub(r"[^\w\u3400-\u9fff\-]+", "", value or "")[:24]
    return cleaned or "考卷"


class PaperStore:
    """Files on the Mac plus the weak-point and practice state."""

    def __init__(self, base=None, today=None, now=None, env=None):
        self.env = os.environ if env is None else env
        self.base = Path(base).expanduser() if base else data_dir(self.env)
        self._today = today or (lambda: datetime.now(HK).date())
        self._now = now or (lambda: datetime.now(HK))

    # -- paths ---------------------------------------------------------------
    @property
    def papers(self):
        return self.base / "papers"

    @property
    def inbox(self):
        return self.papers / "inbox"

    @property
    def reports(self):
        return self.base / "reports"

    @property
    def weak_path(self):
        return self.base / "weak_points.json"

    @property
    def practice_path(self):
        return self.base / "practice.json"

    def today(self):
        return self._today()

    def ensure(self):
        for path in (self.base, self.papers, self.inbox, self.reports):
            _private_dir(path)

    # -- weak points ---------------------------------------------------------
    def items(self):
        raw = _read_json(self.weak_path, {"version": 1, "items": []}).get("items")
        items = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("status") in STATUSES and isinstance(item.get("id"), str):
                items.append({k: v for k, v in item.items() if k in ITEM_KEYS})
        return items

    def save_items(self, items):
        _write(self.weak_path, json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=1))

    def active(self):
        return [item for item in self.items() if item["status"] == "active"]

    def _proposal(self, value, report_id):
        if not isinstance(value, dict) or set(value) - PROPOSAL_KEYS:
            raise ValueError("weak_points: allowed keys are " + ", ".join(sorted(PROPOSAL_KEYS)))
        subject = value.get("subject")
        if subject not in SUBJECTS:
            raise ValueError("weak_points.subject: expected math or chinese_vocab")
        practice = value.get("practice") or ("similar_question" if subject == "math" else "vocab_write")
        if practice not in PRACTICES[subject]:
            raise ValueError("weak_points.practice: " + "/".join(sorted(PRACTICES[subject])))
        item = {
            "subject": subject,
            "label": _text(value.get("label"), "weak_points.label", 40),
            "practice": practice,
            "status": "pending",
            "added": self.today().isoformat(),
            "confirmed_at": None,
            "report_id": report_id,
            "seen": value.get("seen", 2),
            "pass_streak": 0,
            "pass_target": value.get("pass_target", DEFAULT_PASS_TARGET),
            "practiced": 0,
            "passed_at": None,
        }
        for key in ("seen", "pass_target"):
            if type(item[key]) is not int or not 1 <= item[key] <= 20:
                raise ValueError(f"weak_points.{key}: expected 1..20")
        if "mistake" in value:
            item["mistake"] = _text(value["mistake"], "weak_points.mistake", 20)
        if subject == "chinese_vocab":
            chars = value.get("chars")
            if not isinstance(chars, list) or not 1 <= len(chars) <= 8 or not all(
                isinstance(c, str) and len(c) == 1 and "\u3400" <= c <= "\u9fff" for c in chars
            ):
                raise ValueError("weak_points.chars: expected 1-8 Chinese characters")
            item["chars"] = chars
        elif "chars" in value:
            raise ValueError("weak_points.chars: only for chinese_vocab")
        wanted = value.get("id") or (
            "c-vocab-" + "".join(item["chars"]) if subject == "chinese_vocab" else "m-" + secrets.token_hex(3)
        )
        if not isinstance(wanted, str) or not _ID.fullmatch(wanted):
            raise ValueError("weak_points.id: letters, digits, - or _ only")
        item["id"] = wanted
        return item

    def update(self, action, ids):
        """confirm / pause / resume / delete; returns the changed ids."""
        if action not in {"confirm", "pause", "resume", "delete"}:
            raise ValueError("action: expected confirm / pause / resume / delete")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError("ids: expected a non-empty list of weak point ids")
        items = self.items()
        known = {item["id"] for item in items}
        missing = [i for i in ids if i not in known]
        if missing:
            raise LookupError("unknown weak point: " + ", ".join(missing))
        allowed = {"confirm": {"pending"}, "pause": {"active"}, "resume": {"paused"}, "delete": STATUSES}[action]
        changed, kept = [], []
        for item in items:
            if item["id"] in ids and item["status"] in allowed:
                changed.append(item["id"])
                if action == "delete":
                    continue
                if action == "confirm":
                    item["status"], item["confirmed_at"] = "active", self._now().isoformat(timespec="seconds")
                else:
                    item["status"] = "paused" if action == "pause" else "active"
            kept.append(item)
        self.save_items(kept)
        logger.info("Weak points %s: %d changed", action, len(changed))
        return changed

    # -- inbox / reports -----------------------------------------------------
    def list_inbox(self):
        self.ensure()
        files = []
        for path in sorted(self.inbox.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                stat = path.stat()
                files.append({
                    "name": path.name,
                    "path": str(path),
                    "bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, HK).isoformat(timespec="seconds"),
                })
        return files

    def list_reports(self):
        self.ensure()
        out = []
        for path in sorted(self.reports.glob("*.md")):
            out.append({"id": path.stem, "path": str(path), "date": path.stem[:10]})
        return out

    def read_report(self, report_id):
        if not isinstance(report_id, str) or "/" in report_id or report_id.startswith("."):
            raise LookupError("report not found")
        path = self.reports / (report_id + ".md")
        if not path.is_file():
            raise LookupError("report not found")
        return path.read_text(encoding="utf-8")

    def submit_report(self, body):
        """Store dad-facing report, archive inbox photos, add pending weak points."""
        if not isinstance(body, dict) or set(body) - {"date", "subject", "name", "markdown", "inbox_files", "weak_points"}:
            raise ValueError("report: allowed keys are date, subject, name, markdown, inbox_files, weak_points")
        day = body.get("date") or self.today().isoformat()
        if not isinstance(day, str) or not _DAY.fullmatch(day):
            raise ValueError("date: expected YYYY-MM-DD")
        date.fromisoformat(day)
        subject = body.get("subject")
        if subject not in SUBJECTS:
            raise ValueError("subject: expected math or chinese_vocab")
        markdown = body.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > 20000:
            raise ValueError("markdown: expected report text")
        files = body.get("inbox_files", [])
        proposals = body.get("weak_points", [])
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise ValueError("inbox_files: expected a list of inbox file names")
        if not isinstance(proposals, list) or len(proposals) > MAX_WEAK_POINTS_PER_REPORT:
            raise ValueError(f"weak_points: at most {MAX_WEAK_POINTS_PER_REPORT} per report")
        self.ensure()
        sources = []
        for name in files:
            path = self.inbox / name
            if Path(name).name != name or not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                raise LookupError(f"inbox file not found: {name}")
            sources.append(path)
        stem = f"{day}-{SUBJECTS[subject]}-{_safe_name(body.get('name'))}"
        report_id, n = stem, 2
        while (self.reports / (report_id + ".md")).exists():
            report_id, n = f"{stem}-{n}", n + 1
        new = [self._proposal(value, report_id) for value in proposals]
        items = self.items()
        by_id = {item["id"]: item for item in items}
        pending, merged = [], []
        for item in new:
            old = by_id.get(item["id"])
            if old is None:
                items.append(item)
                by_id[item["id"]] = item
                pending.append(item["id"])
            elif old["status"] == "pending":
                old.update({k: item[k] for k in ("label", "practice", "report_id") if k in item})
                old["seen"] = max(old.get("seen", 1), item["seen"])
                pending.append(item["id"])
            else:
                # Already confirmed / passed: just remember it came up again.
                old["seen"] = old.get("seen", 1) + 1
                merged.append(item["id"])
        _write(self.reports / (report_id + ".md"), markdown.rstrip() + "\n")
        archived = []
        if sources:
            folder = self.papers / f"{day}-{report_id[11:]}"
            _private_dir(folder)
            for path in sources:
                target = folder / path.name
                os.replace(path, target)
                target.chmod(0o600)
                archived.append(str(target))
        self.save_items(items)
        logger.info("Paper report stored: %d photos, %d pending weak points", len(archived), len(pending))
        return {"report_id": report_id, "report_path": str(self.reports / (report_id + ".md")),
                "photos": archived, "pending": pending, "seen_again": merged}

    # -- retention -----------------------------------------------------------
    def purge(self):
        """Photos after 90 days; reports, weak points and logs after school-year end."""
        today = self.today()
        end = school_year_end(self.env)
        removed = {"photo_folders": 0, "inbox_photos": 0, "reports": 0, "weak_points": 0}
        if self.papers.is_dir():
            cutoff = today - timedelta(days=PHOTO_RETENTION_DAYS)
            for path in self.papers.iterdir():
                if path.is_dir() and path.name != "inbox" and _DAY.match(path.name[:10]):
                    try:
                        if date.fromisoformat(path.name[:10]) < cutoff:
                            shutil.rmtree(path)
                            removed["photo_folders"] += 1
                    except ValueError:
                        continue
            if self.inbox.is_dir():
                for path in self.inbox.iterdir():
                    if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, HK).date() < cutoff:
                        path.unlink()
                        removed["inbox_photos"] += 1
        if self.reports.is_dir():
            for path in self.reports.glob("*.md"):
                try:
                    if year_end_after(date.fromisoformat(path.name[:10]), end) < today:
                        path.unlink()
                        removed["reports"] += 1
                except ValueError:
                    continue
        if self.weak_path.exists():
            items = self.items()
            kept = []
            for item in items:
                try:
                    expired = year_end_after(date.fromisoformat(item.get("added", "")), end) < today
                except (TypeError, ValueError):
                    expired = False
                if not expired:
                    kept.append(item)
            if len(kept) != len(items):
                removed["weak_points"] = len(items) - len(kept)
                self.save_items(kept)
        if any(removed.values()):
            logger.info("Paper retention purge: %s", removed)
        return removed

    def delete_all(self, confirm=False, expect=None):
        """Two-step wipe: list first, delete only with confirm and the same count."""
        files = []
        for root in (self.papers, self.reports):
            if root.is_dir():
                files += [str(p) for p in sorted(root.rglob("*")) if p.is_file()]
        for path in (self.weak_path, self.practice_path):
            if path.is_file():
                files.append(str(path))
        if not confirm:
            return {"deleted": False, "files": files, "count": len(files)}
        if expect != len(files):
            raise ValueError("expect: file count changed; list again before deleting")
        for root in (self.papers, self.reports):
            if root.exists():
                shutil.rmtree(root)
        for path in (self.weak_path, self.practice_path):
            path.unlink(missing_ok=True)
        logger.info("Paper data deleted by parent: %d files", len(files))
        return {"deleted": True, "count": len(files)}

    # -- pre-exam revision list ----------------------------------------------
    def revision(self, subject=None):
        if subject is not None and subject not in SUBJECTS:
            raise ValueError("subject: expected math or chinese_vocab")
        pool = [i for i in self.items() if i["status"] in {"active", "paused"} and subject in (None, i["subject"])]
        pool.sort(key=lambda i: (i["subject"] != "chinese_vocab", -i.get("seen", 1), i.get("pass_streak", 0), i["id"]))
        titles = []
        for item in pool:
            topic = ("生字 " + "、".join(item["chars"])) if item.get("chars") else item["label"]
            titles.append("溫書：" + topic)
        return {"titles": titles, "ids": [i["id"] for i in pool]}

    # -- weekly progress -----------------------------------------------------
    def weekly(self, day=None):
        day = day or self.today()
        start = day - timedelta(days=6)
        items = self.items()
        log = Practice(self).state().get("log", {})
        practiced = sum(v.get("started", 0) for k, v in log.items() if start.isoformat() <= k <= day.isoformat())
        abandoned = sum(v.get("abandoned", 0) for k, v in log.items() if start.isoformat() <= k <= day.isoformat())
        passed = [i["label"] for i in items if i["status"] == "passed" and (i.get("passed_at") or "") >= start.isoformat()]
        active = [
            {"label": i["label"], "pass_streak": i.get("pass_streak", 0), "pass_target": i.get("pass_target", DEFAULT_PASS_TARGET)}
            for i in items if i["status"] == "active"
        ]
        suggest = [i["label"] for i in sorted(
            (i for i in items if i["status"] == "active"), key=lambda i: (-i.get("seen", 1), i.get("pass_streak", 0)))][:3]
        return {
            "from": start.isoformat(), "to": day.isoformat(), "active": active, "passed": passed,
            "practiced": practiced, "abandoned": abandoned,
            "pending": sum(1 for i in items if i["status"] == "pending"), "suggest": suggest,
        }


def format_weekly(week):
    if not week or not (week["active"] or week["passed"] or week["practiced"] or week["pending"]):
        return ""
    lines = [f"【考卷弱點：本週進度】{week['from']} 至 {week['to']}"]
    lines.append(f"本週練習：{week['practiced']} 題" + (f"（放棄 {week['abandoned']} 題）" if week["abandoned"] else ""))
    if week["passed"]:
        lines.append("本週練熟咗：" + "、".join(week["passed"]))
    if week["active"]:
        lines.append("仲練緊：" + "；".join(f"{a['label']}（連續啱 {a['pass_streak']}/{a['pass_target']}）" for a in week["active"]))
    if week["pending"]:
        lines.append(f"等你確認：{week['pending']} 個弱點")
    if week["suggest"]:
        lines.append("建議下週留意：" + "、".join(week["suggest"]))
    return "\n".join(lines)


def weekly_due(day, env=None):
    env = os.environ if env is None else env
    try:
        weekday = int(env.get("STACKCHAN_WEEKLY_REPORT_WEEKDAY", "6"))
    except ValueError:
        weekday = 6
    return day.weekday() == weekday


class Practice:
    """Daily 2-question practice from confirmed weak points.

    Rules (plan §4.2): only after a related homework item is done; at most two
    a day (dad may add more for today only); a started question counts even if
    abandoned; no make-up; same question resumes after she comes back; it never
    touches homework progress, 鈴鈴's form or games.
    """

    def __init__(self, store):
        self.store = store

    def state(self):
        state = _read_json(self.store.practice_path, {})
        today = self.store.today().isoformat()
        log = state.get("log") if isinstance(state.get("log"), dict) else {}
        if state.get("date") != today:
            if isinstance(state.get("open"), dict) and isinstance(state.get("date"), str):
                entry = log.setdefault(state["date"], {})
                entry["abandoned"] = entry.get("abandoned", 0) + 1
            state = {"date": today, "used": 0, "extra": 0, "open": None, "due": None, "log": log}
        cutoff = (self.store.today() - timedelta(days=PRACTICE_LOG_DAYS)).isoformat()
        state["log"] = {k: v for k, v in log.items() if isinstance(v, dict) and k >= cutoff}
        return state

    def _save(self, state):
        _write(self.store.practice_path, json.dumps(state, ensure_ascii=False))

    def _limit(self, state):
        return DAILY_LIMIT + state.get("extra", 0)

    def is_open(self):
        try:
            return bool(self.state().get("open"))
        except OSError:
            return False

    @staticmethod
    def view(item):
        """The only weak-point fields the child side ever sees."""
        out = {"subject": item["subject"], "topic": item["label"], "practice": item["practice"]}
        if item.get("chars"):
            out["chars"] = item["chars"]
        if item.get("mistake"):
            out["mistake_type"] = item["mistake"]
        return out

    def topics(self):
        return [self.view(i) for i in self.store.active()]

    def _pick(self, subject):
        candidates = [i for i in self.store.active() if subject in (None, i["subject"])]
        candidates.sort(key=lambda i: (i.get("practiced", 0), -i.get("seen", 1), i["id"]))
        return candidates[0] if candidates else None

    def note_homework_done(self, title):
        """Mark practice due after a related item; returns flags for the result."""
        title = (title or "").lower()
        subjects = [s for s, words in SUBJECT_KEYWORDS.items() if any(w in title for w in words)]
        state = self.state()
        if not subjects or state["open"] or state["used"] >= self._limit(state):
            return {}
        for subject in subjects:
            item = self._pick(subject)
            if item:
                state["due"] = item["id"]
                self._save(state)
                return {"practice_due": True}
        return {}

    def add_extra(self, count):
        if type(count) is not int or not 1 <= count <= 5:
            raise ValueError("extra: expected 1..5")
        state = self.state()
        state["extra"] = state.get("extra", 0) + count
        self._save(state)
        return {"date": state["date"], "limit": self._limit(state), "used": state["used"]}

    def _item(self, item_id):
        return next((i for i in self.store.items() if i["id"] == item_id), None)

    def start(self):
        state = self.state()
        if state["open"]:
            item = self._item(state["open"]["id"])
            if item and item["status"] == "active":
                return {"ok": True, "practice": True, "resumed": True, "question": self.view(item)}
            state["open"] = None
        if state["used"] >= self._limit(state):
            self._save(state)
            return {"ok": True, "practice": False, "reason": "daily_limit", "spoken": "今日練夠喇，聽日再嚟。"}
        item = self._item(state["due"]) if state.get("due") else None
        if not item or item["status"] != "active":
            state["due"] = None
            self._save(state)
            return {"ok": True, "practice": False, "reason": "not_due", "spoken": ""}
        state["used"] += 1
        state["due"] = None
        state["open"] = {"id": item["id"], "refusals": 0}
        entry = state["log"].setdefault(state["date"], {})
        entry["started"] = entry.get("started", 0) + 1
        self._save(state)
        return {"ok": True, "practice": True, "resumed": False, "question": self.view(item),
                "left_today": self._limit(state) - state["used"]}

    def refuse(self):
        state = self.state()
        if not state["open"]:
            return {"ok": False, "error": "no_practice_open"}
        state["open"]["refusals"] += 1
        if state["open"]["refusals"] < 2:
            self._save(state)
            return {"ok": True, "practice": True, "spoken": "得啦，好快㗎，我陪你。"}
        state["open"] = None
        entry = state["log"].setdefault(state["date"], {})
        entry["abandoned"] = entry.get("abandoned", 0) + 1
        self._save(state)
        return {"ok": True, "practice": False, "abandoned": True, "spoken": ""}

    def answer(self, correct):
        if type(correct) is not bool:
            return {"ok": False, "error": "correct: expected true/false"}
        state = self.state()
        if not state["open"]:
            return {"ok": False, "error": "no_practice_open"}
        item_id = state["open"]["id"]
        state["open"] = None
        entry = state["log"].setdefault(state["date"], {})
        entry["correct" if correct else "wrong"] = entry.get("correct" if correct else "wrong", 0) + 1
        items = self.store.items()
        passed = False
        for item in items:
            if item["id"] == item_id:
                item["practiced"] = item.get("practiced", 0) + 1
                item["pass_streak"] = item.get("pass_streak", 0) + 1 if correct else 0
                if correct and item["pass_streak"] >= item.get("pass_target", DEFAULT_PASS_TARGET):
                    item["status"], item["passed_at"], passed = "passed", self.store.today().isoformat(), True
        self.store.save_items(items)
        self._save(state)
        spoken = "呢類題你而家好熟手喎！" if passed else ("中咗！好叻呀。" if correct else "")
        return {"ok": True, "practice": False, "correct": correct, "mastered": passed, "spoken": spoken,
                "left_today": max(0, self._limit(state) - state["used"])}

    def status(self):
        state = self.state()
        item = self._item(state["open"]["id"]) if state["open"] else None
        return {"ok": True, "open": bool(item), "question": self.view(item) if item else None,
                "due": bool(state.get("due")), "left_today": max(0, self._limit(state) - state["used"])}


_store = None


def get_paper_store():
    global _store
    if _store is None:
        _store = PaperStore()
    return _store


def reset_paper_store():
    global _store
    _store = None
