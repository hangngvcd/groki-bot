"""考卷分析 v1: storage, retention, dad-confirm gate, kid practice rules, parent tools."""

import asyncio
import io
import json
import os
import stat
import time
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from stackchan_mcp import paper_analysis
from stackchan_mcp.paper_analysis import (
    HK,
    PaperStore,
    Practice,
    format_weekly,
    get_paper_store,
    weekly_due,
    year_end_after,
)
from stackchan_mcp.parent_api import create_parent_app
from stackchan_mcp.parent_cli import main, run_mcp
from stackchan_mcp.parent_settings import ParentSettings
from stackchan_mcp.xiaoya_forms import XiaoyaForms

MATH = {"id": "m-frac-add", "subject": "math", "label": "分數加減（異分母）", "practice": "similar_question", "seen": 3}
VOCAB = {"subject": "chinese_vocab", "label": "蝴蝶", "chars": ["蝴", "蝶"], "mistake": "同音別字"}


@pytest.fixture
def today():
    return [date(2026, 11, 3)]


@pytest.fixture
def store(tmp_path, today):
    return PaperStore(tmp_path / "data", today=lambda: today[0],
                      now=lambda: datetime(2026, 11, 3, 20, 0, tzinfo=HK), env={})


def photo(store, name="IMG_1.jpg"):
    store.ensure()
    path = store.inbox / name
    path.write_bytes(b"jpeg")
    return path


def submit(store, *points, files=()):
    return store.submit_report({
        "date": "2026-11-03", "subject": "math", "name": "數學測驗",
        "markdown": "# 數學測驗\n分數加減（異分母）錯咗兩題", "inbox_files": list(files),
        "weak_points": list(points),
    })


def confirmed(store, *points):
    result = submit(store, *points)
    store.update("confirm", result["pending"])
    return result


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_default_dir_and_env(monkeypatch, tmp_path):
    assert paper_analysis.data_dir({}) == paper_analysis.Path("~/xiaoya-data").expanduser()
    assert paper_analysis.data_dir({"STACKCHAN_XIAOYA_DATA_DIR": str(tmp_path)}) == tmp_path
    assert get_paper_store().base == tmp_path / "xiaoya-data"  # conftest isolation


def test_submit_archives_photos_private_and_pending(store):
    photo(store)
    result = submit(store, MATH, VOCAB, files=["IMG_1.jpg"])
    assert result["report_id"] == "2026-11-03-數學-數學測驗"
    report = store.reports / "2026-11-03-數學-數學測驗.md"
    assert report.read_text().startswith("# 數學測驗")
    archived = paper_analysis.Path(result["photos"][0])
    assert archived.parent.name == "2026-11-03-數學-數學測驗" and archived.read_bytes() == b"jpeg"
    assert not (store.inbox / "IMG_1.jpg").exists()
    assert mode(store.base) == mode(store.reports) == mode(archived.parent) == 0o700
    assert mode(report) == mode(store.weak_path) == mode(archived) == 0o600
    assert result["pending"] == ["m-frac-add", "c-vocab-蝴蝶"]
    assert {i["status"] for i in store.items()} == {"pending"}
    assert store.active() == []
    raw = store.weak_path.read_text()
    assert "jpg" not in raw and "papers" not in raw
    # A second report of the same day gets its own file.
    assert submit(store)["report_id"] == "2026-11-03-數學-數學測驗-2"


@pytest.mark.parametrize("bad", [
    {**MATH, "label": "分數加減 18/20"},
    {**MATH, "label": "得 85分"},
    {**MATH, "label": "滿分差少少"},
    {**MATH, "score": 18},
    {**MATH, "subject": "english"},
    {**MATH, "practice": "vocab_write"},
    {**VOCAB, "chars": ["ab"]},
    {**MATH, "chars": ["分"]},
    {**MATH, "id": "../x"},
])
def test_rejects_scores_and_bad_items(store, bad):
    with pytest.raises(ValueError):
        submit(store, bad)
    assert not store.weak_path.exists()


def test_limits_and_unknown_inbox(store):
    # No cap on weak points per report: all proposals are kept as pending.
    many = [{**MATH, "id": f"m-{n}", "label": f"題型{n}"} for n in range(2, 7)]
    assert len(submit(store, MATH, VOCAB, *many)["pending"]) == 7
    with pytest.raises(LookupError):
        submit(store, files=["missing.jpg"])
    with pytest.raises(LookupError):
        submit(store, files=["../weak_points.json"])
    # 分數 / 分母 are topics, not scores.
    assert submit(store, {**MATH, "label": "通分（分母唔同）"})["pending"] == ["m-frac-add"]


def test_confirm_pause_resume_delete(store):
    submit(store, MATH, VOCAB)
    assert store.update("confirm", ["m-frac-add"]) == ["m-frac-add"]
    assert [i["id"] for i in store.active()] == ["m-frac-add"]
    assert store.update("pause", ["m-frac-add"]) == ["m-frac-add"]
    assert store.active() == []
    assert store.update("resume", ["m-frac-add"]) == ["m-frac-add"]
    assert store.update("delete", ["c-vocab-蝴蝶"]) == ["c-vocab-蝴蝶"]
    with pytest.raises(LookupError):
        store.update("confirm", ["nope"])
    with pytest.raises(ValueError):
        store.update("promote", ["m-frac-add"])
    # Seen again on a later paper: stays active, count goes up.
    submit(store, MATH)
    item = store.items()[0]
    assert item["status"] == "active" and item["seen"] == 4


def test_retention(store, today, tmp_path):
    photo(store, "old.jpg")
    submit(store, MATH, files=["old.jpg"])
    store.update("confirm", ["m-frac-add"])
    stale = photo(store, "stale.jpg")
    old = time.time() - 120 * 86400
    os.utime(stale, (old, old))
    today[0] = date(2027, 2, 1)  # exactly 90 days: kept
    removed = store.purge()
    assert removed["photo_folders"] == 0 and removed["inbox_photos"] == 1  # inbox goes by file age
    today[0] = date(2027, 2, 2)
    assert store.purge()["photo_folders"] == 1
    assert store.list_reports() and store.items()
    today[0] = date(2027, 8, 31)  # last day of the school year: still kept
    assert store.purge()["reports"] == 0
    today[0] = date(2027, 9, 1)
    removed = store.purge()
    assert removed["reports"] == 1 and removed["weak_points"] == 1
    assert year_end_after(date(2026, 9, 1), (8, 31)) == date(2027, 8, 31)
    assert paper_analysis.school_year_end({"STACKCHAN_SCHOOL_YEAR_END": "07-15"}) == (7, 15)
    assert paper_analysis.school_year_end({"STACKCHAN_SCHOOL_YEAR_END": "bad"}) == (8, 31)


def test_delete_all_two_steps(store):
    photo(store)
    submit(store, MATH, files=["IMG_1.jpg"])
    listing = store.delete_all()
    assert listing["deleted"] is False and listing["count"] == 3
    assert store.weak_path.exists()
    with pytest.raises(ValueError):
        store.delete_all(True, expect=2)
    assert store.delete_all(True, expect=3) == {"deleted": True, "count": 3}
    assert not store.weak_path.exists() and not store.reports.exists()


def test_revision_order(store):
    submit(store, {**MATH, "seen": 2}, {**MATH, "id": "m-unit", "label": "單位換算", "seen": 5}, VOCAB)
    store.update("confirm", ["m-frac-add", "m-unit", "c-vocab-蝴蝶"])
    proposal = store.revision()
    assert proposal["titles"] == ["溫書：生字 蝴、蝶", "溫書：單位換算", "溫書：分數加減（異分母）"]
    assert store.revision("math")["ids"] == ["m-unit", "m-frac-add"]
    with pytest.raises(ValueError):
        store.revision("art")


def test_practice_rules(store):
    practice = Practice(store)
    submit(store, MATH)
    # Pending only: nothing for the kid even after a related item.
    assert practice.note_homework_done("數學練習") == {}
    store.update("confirm", ["m-frac-add"])
    assert practice.start()["reason"] == "not_due"
    assert practice.note_homework_done("英文默書") == {}  # unrelated subject
    assert practice.note_homework_done("數學練習") == {"practice_due": True}
    started = practice.start()
    assert started["practice"] and not started["resumed"]
    assert started["question"] == {"subject": "math", "topic": "分數加減（異分母）", "practice": "similar_question"}
    assert not {"seen", "report_id", "added", "status"} & set(started["question"])
    # She leaves and comes back: same question, no extra quota.
    again = practice.start()
    assert again["resumed"] and again["question"] == started["question"]
    assert practice.state()["used"] == 1
    first = practice.refuse()
    assert first["practice"] and first["spoken"] == "得啦，好快㗎，我陪你。"
    second = practice.refuse()
    assert second["abandoned"] and second["spoken"] == ""
    # Abandoned still counts; one left.
    assert practice.status()["left_today"] == 1
    practice.note_homework_done("數學")
    practice.start()
    result = practice.answer(True)
    assert result["spoken"] == "中咗！好叻呀。" and result["left_today"] == 0
    assert practice.note_homework_done("數學") == {}
    limit = practice.start()
    assert limit["practice"] is False and limit["spoken"] == "今日練夠喇，聽日再嚟。"
    assert practice.add_extra(1)["limit"] == 3
    with pytest.raises(ValueError):
        practice.add_extra(0)


def test_practice_streak_pass_and_day_rollover(store, today):
    practice = Practice(store)
    confirmed(store, {**MATH, "pass_target": 2})

    def one(correct):
        assert practice.note_homework_done("數學") == {"practice_due": True}
        practice.start()
        return practice.answer(correct)

    assert one(True)["mastered"] is False
    assert one(False)["spoken"] == ""
    assert store.items()[0]["pass_streak"] == 0
    today[0] = date(2026, 11, 4)  # new day, fresh quota
    one(True)
    done = one(True)
    assert done["mastered"] and done["spoken"] == "呢類題你而家好熟手喎！"
    item = store.items()[0]
    assert item["status"] == "passed" and item["passed_at"] == "2026-11-04"
    today[0] = date(2026, 11, 5)
    assert practice.note_homework_done("數學") == {}  # passed items never return
    # Open question left at midnight is closed as abandoned.
    confirmed(store, VOCAB)
    practice.note_homework_done("中文生字")
    practice.start()
    today[0] = date(2026, 11, 6)
    state = practice.state()
    assert state["open"] is None and state["used"] == 0
    assert state["log"]["2026-11-05"]["abandoned"] == 1


def test_weekly(store, today):
    assert format_weekly(store.weekly()) == ""
    practice = Practice(store)
    confirmed(store, {**MATH, "pass_target": 1}, VOCAB)
    practice.note_homework_done("數學")
    practice.start()
    practice.answer(True)
    submit(store, {**MATH, "id": "m-new", "label": "小數"})
    week = store.weekly()
    assert week["practiced"] == 1 and week["passed"] == ["分數加減（異分母）"]
    assert week["pending"] == 1 and [a["label"] for a in week["active"]] == ["蝴蝶"]
    text = format_weekly(week)
    assert "本週練熟咗：分數加減（異分母）" in text and "等你確認：1 個弱點" in text
    assert "分數加減（異分母）" in text and "/20" not in text
    assert weekly_due(date(2026, 11, 8), {}) and not weekly_due(date(2026, 11, 3), {})
    assert weekly_due(date(2026, 11, 3), {"STACKCHAN_WEEKLY_REPORT_WEEKDAY": "1"})


def test_homework_completion_marks_practice_due_without_touching_forms(tmp_path, monkeypatch):
    from stackchan_mcp import camera_presence, xiaoya_games
    from stackchan_mcp.camera_presence import CameraPresence
    from stackchan_mcp.homework_photo import HomeworkPhoto
    from stackchan_mcp.xiaoya_games import XiaoyaGames

    store = get_paper_store()
    confirmed(store, MATH)
    presence = CameraPresence()
    monkeypatch.setattr(camera_presence, "_presence", presence)
    monkeypatch.setattr(xiaoya_games, "_games", XiaoyaGames(tmp_path / "games.json"))
    forms = XiaoyaForms(tmp_path / "forms.json", today=lambda: "2026-10-27")
    forms.add_items(["數學", "中文"], source="test")
    flow = HomeworkPhoto(forms=forms, presence=presence, capture=lambda: b"jpeg", assess=lambda p: "ok",
                         send=lambda *a: None, captures_dir=tmp_path / "captures")
    flow.resolve("#1")
    result = flow.attempt_capture()
    assert result["ok"] and result["practice_due"] is True
    before = forms.snapshot()
    practice = Practice(store)
    practice.start()
    practice.answer(True)
    after = forms.snapshot()
    assert (after["done"], after["form"], after["total"]) == (before["done"], before["form"], before["total"])
    assert not xiaoya_games.get_xiaoya_games().has_pending() or before == after


@pytest.mark.asyncio
async def test_kid_tool_catalog_rules_and_stuck_suppressed(monkeypatch, tmp_path):
    from stackchan_mcp.stuck_notify import StuckNotify
    from stackchan_mcp.voice_tools import VoiceToolExecutor

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    sent = []
    stuck = StuckNotify(path=tmp_path / "stuck.json", today=lambda: date(2026, 11, 3),
                        send=lambda *a: sent.append(a))
    monkeypatch.setattr("stackchan_mcp.stuck_notify.get_stuck_notify", lambda: stuck)
    store = get_paper_store()
    confirmed(store, MATH)
    executor = VoiceToolExecutor()
    try:
        assert "paper_practice" in {d["name"] for d in executor.declarations()}
        text = executor.instructions()
        assert "做完喇！我哋練吓測驗嗰類分數題，一題就好。" in text
        assert "唔好問「要唔要」" in text and "永遠唔講分數" in text
        listing = await executor.execute("homework_list", {})
        assert listing.get("practice_topics") is None  # list empty -> early return
        Practice(store).note_homework_done("數學")
        started = await executor.execute("paper_practice", {"action": "start"})
        assert started["practice"] and "score" not in json.dumps(started)
        for _ in range(4):
            result = await executor.execute("note_homework_stuck", {"item": "數學"})
            assert result["offer_help"] is False
        assert not stuck.path.exists() or "數學" not in stuck.path.read_text()
        done = await executor.execute("paper_practice", {"action": "answer", "correct": False})
        assert done["ok"] and done["correct"] is False
        assert (await executor.execute("paper_practice", {"action": "bogus"}))["ok"] is False
        assert (await executor.execute("paper_practice", {"action": "answer", "correct": True}))["ok"] is False
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_homework_list_carries_topics(monkeypatch):
    from stackchan_mcp.voice_tools import VoiceToolExecutor
    from stackchan_mcp.xiaoya_forms import get_xiaoya_forms

    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    get_xiaoya_forms().add_items(["數學"], source="test")
    store = get_paper_store()
    submit(store, MATH)
    executor = VoiceToolExecutor()
    try:
        assert "practice_topics" not in await executor.execute("homework_list", {})
        store.update("confirm", ["m-frac-add"])
        listing = await executor.execute("homework_list", {})
        assert listing["practice_topics"] == ["分數加減（異分母）"] and "practice_due" not in listing
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_parent_api_cli_and_usage_weekly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STACKCHAN_TOKEN", "child-secret")
    monkeypatch.setenv("STACKCHAN_PARENT_TOKEN", "parent-secret")
    store = PaperStore(tmp_path / "data", env={})
    forms = XiaoyaForms(tmp_path / "forms.json")
    usage = SimpleNamespace(snapshot=lambda day=None: None, now=lambda: datetime(2026, 11, 3, tzinfo=HK))
    photo(store)
    app = create_parent_app("parent-secret", ParentSettings(tmp_path / "s.json"), forms=forms, usage=usage, papers=store)
    async with TestClient(TestServer(app)) as client:
        auth = {"Authorization": "Bearer parent-secret"}
        assert (await client.get("/parent/v1/papers/inbox")).status == 401
        inbox = await (await client.get("/parent/v1/papers/inbox", headers=auth)).json()
        assert [f["name"] for f in inbox["inbox"]] == ["IMG_1.jpg"]
        bad = await client.post("/parent/v1/papers/report", headers=auth, json={"subject": "math", "markdown": "x", "weak_points": [{**MATH, "label": "18/20"}]})
        assert bad.status == 400
        missing = await client.post("/parent/v1/papers/report", headers=auth, json={"subject": "math", "markdown": "x", "inbox_files": ["nope.jpg"]})
        assert missing.status == 404
        body = {"date": "2026-11-03", "subject": "math", "name": "測驗", "markdown": "# r", "inbox_files": ["IMG_1.jpg"], "weak_points": [MATH]}
        stored = await (await client.post("/parent/v1/papers/report", headers=auth, json=body)).json()
        assert stored["ok"] and stored["pending"] == ["m-frac-add"]
        reports = await (await client.get("/parent/v1/papers/reports", headers=auth)).json()
        assert reports["reports"][0]["id"] == stored["report_id"]
        one = await (await client.get("/parent/v1/papers/reports", headers=auth, params={"id": stored["report_id"]})).json()
        assert one["markdown"] == "# r\n"
        assert (await client.get("/parent/v1/papers/reports", headers=auth, params={"id": "../weak_points"})).status == 404
        assert (await client.post("/parent/v1/weak-points", headers=auth, json={"action": "confirm"})).status == 400
        confirmed_ = await (await client.post("/parent/v1/weak-points", headers=auth, json={"action": "confirm", "ids": ["m-frac-add"]})).json()
        assert confirmed_["weak_points"][0]["status"] == "active"
        extra = await (await client.post("/parent/v1/practice/extra", headers=auth, json={"count": 1})).json()
        assert extra["limit"] == 3
        proposal = await (await client.post("/parent/v1/papers/revision", headers=auth, json={})).json()
        assert proposal["proposal"]["titles"] == ["溫書：分數加減（異分母）"] and forms.snapshot()["total"] == 0
        added = await (await client.post("/parent/v1/papers/revision", headers=auth, json={"confirm": True, "titles": proposal["proposal"]["titles"]})).json()
        assert [i["title"] for i in added["forms"]["items"]] == ["溫書：分數加減（異分母）"]
        # Usage: weekly section only on the weekly day or when asked; usage record untouched.
        plain = await (await client.get("/parent/v1/usage", headers=auth)).json()
        assert "weekly_weak_points" not in plain and plain["usage"] is None
        weekly = await (await client.get("/parent/v1/usage", headers=auth, params={"weekly": "1"})).json()
        assert "仲練緊：分數加減（異分母）" in weekly["weekly_weak_points"]["text"]
        base = str(client.make_url("")).rstrip("/")
        assert await asyncio.to_thread(main, ["--url", base, "weak-points"]) == 0
        assert "m-frac-add" in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "weak-points", "confirm"]) == 1
        capsys.readouterr()
        assert await asyncio.to_thread(main, ["--url", base, "papers", "inbox"]) == 0
        assert '"inbox": []' in capsys.readouterr().out
        assert await asyncio.to_thread(main, ["--url", base, "usage", "--weekly", "--text"]) == 0
        out = capsys.readouterr().out
        assert "【考卷弱點：本週進度】" in out
        assert await asyncio.to_thread(main, ["--url", base, "papers", "delete-all"]) == 0
        count = json.loads(capsys.readouterr().out)["count"]
        assert await asyncio.to_thread(main, ["--url", base, "papers", "delete-all", "--yes", str(count + 1)]) == 1
        capsys.readouterr()
        assert await asyncio.to_thread(main, ["--url", base, "papers", "delete-all", "--yes", str(count)]) == 0
        assert not store.weak_path.exists()
        report = tmp_path / "r.json"
        report.write_text(json.dumps({"subject": "chinese_vocab", "markdown": "# 默書", "weak_points": [VOCAB]}))
        assert await asyncio.to_thread(main, ["--url", base, "papers", "submit", str(report)]) == 0
        assert store.items()[0]["id"] == "c-vocab-蝴蝶"
        assert await asyncio.to_thread(main, ["--url", base, "papers", "submit", str(tmp_path / "none.json")]) == 1
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"subject": "math", "markdown": "# 測驗", "weak_points": [MATH]})))
        assert await asyncio.to_thread(main, ["--url", base, "papers", "submit", "-"]) == 0
        assert "m-frac-add" in {i["id"] for i in store.items()}


@pytest.mark.asyncio
async def test_parent_mcp_paper_tools(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    async def inline(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline)
    calls = []

    class Client:
        def call(self, endpoint, values=None):
            calls.append((endpoint, values))
            return {"ok": True}

    async def run(server):
        tool = lambda name: server._tool_manager.get_tool(name).fn
        names = {t.name for t in await server.list_tools()}
        assert {"xiaoya_papers_inbox", "xiaoya_papers_submit_report", "xiaoya_weak_points",
                "xiaoya_weak_points_update", "xiaoya_practice_extra", "xiaoya_papers_revision",
                "xiaoya_papers_delete_all", "xiaoya_papers_reports"} <= names
        await tool("xiaoya_papers_inbox")()
        await tool("xiaoya_papers_submit_report")(subject="math", markdown="# r", weak_points=[MATH])
        await tool("xiaoya_weak_points_update")(action="confirm", ids=["m-frac-add"])
        await tool("xiaoya_papers_revision")(subject="math")
        await tool("xiaoya_papers_revision")(confirm=True, titles=["溫書：x"])
        await tool("xiaoya_papers_delete_all")()
        await tool("xiaoya_papers_delete_all")(confirm=True, expect=3)
        await tool("xiaoya_practice_extra")()

    monkeypatch.setattr(FastMCP, "run_stdio_async", run)
    await run_mcp(Client())
    assert calls == [
        ("papers/inbox", None),
        ("papers/report", {"subject": "math", "markdown": "# r", "name": "", "inbox_files": [], "weak_points": [MATH]}),
        ("weak-points", {"action": "confirm", "ids": ["m-frac-add"]}),
        ("papers/revision", {"subject": "math"}),
        ("papers/revision", {"confirm": True, "titles": ["溫書：x"]}),
        ("papers/delete-all", {"confirm": False}),
        ("papers/delete-all", {"confirm": True, "expect": 3}),
        ("practice/extra", {"count": 1}),
    ]
