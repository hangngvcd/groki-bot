# SPDX-License-Identifier: MIT
import asyncio
import base64
import pytest_asyncio
from concurrent.futures import ThreadPoolExecutor
import pytest
from stackchan_mcp.photo_capture import PhotoCapture, MAX_BYTES

@pytest_asyncio.fixture(autouse=True)
async def loop_tick():
    # Keep the receive loop ticking even on runners that restrict self-pipe wakeups.
    async def tick():
        while True:
            await asyncio.sleep(0.005)
    task = asyncio.create_task(tick())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def run_capture(adapter):
    # Explicit executor keeps test teardown independent of asyncio's global pool.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return await asyncio.get_running_loop().run_in_executor(pool, adapter.capture)


JPEG = b"\xff\xd8" + bytes(range(256)) * 30 + b"\xff\xd9"


def event(nonce, status, **fields):
    return {"type": "xiaoya", "event": "homework_photo", "nonce": nonce,
            "status": status, **fields}


@pytest.mark.asyncio
@pytest.mark.parametrize("chunked", [False, True])
async def test_roundtrip_on_worker_while_loop_receives(chunked):
    adapter = PhotoCapture(timeout=1)
    messages = []

    async def sender(message):
        messages.append(message)
        if message["type"] == "system":
            nonce = message["nonce"]
            assert 16 <= len(nonce) <= 128
            # Unsolicited/replayed and unknown nonces never satisfy the waiter.
            adapter.note_device_event(event("stale", "capture_failed"))
            if chunked:
                chunks = [JPEG[i:i+3072] for i in range(0, len(JPEG), 3072)]
                adapter.note_device_event(event(nonce, "uploading", bytes=len(JPEG), chunks=len(chunks),
                                                mime="image/jpeg", encoding="base64"))
                for i, data in enumerate(chunks):
                    await asyncio.sleep(0)
                    adapter.note_device_event(event(nonce, "chunk", i=i, data=base64.b64encode(data).decode()))
                adapter.note_device_event(event(nonce, "complete"))
            else:
                adapter.note_device_event(event(nonce, "frame", mime="image/jpeg", encoding="base64",
                                                data=base64.b64encode(JPEG).decode()))
        return True

    adapter.bind(sender, asyncio.get_running_loop())
    assert await run_capture(adapter) == JPEG
    assert [m.get("phase") or m.get("command") for m in messages] == ["shutter", "xiaoya.photo"]
    assert not adapter._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"status": "camera_unavailable"}, {"status": "capture_failed"}, {"status": "too_large"},
    {"status": "uploading", "bytes": MAX_BYTES+1, "chunks": 1},
    {"status": "chunk", "i": 0, "data": "YQ=="},
    {"status": "complete"},
    {"status": "frame", "mime": "image/jpeg", "encoding": "base64", "data": "%%%%"},
    {"status": "frame", "mime": "image/jpeg", "encoding": "base64", "data": "YQ=="},
    {"status": "frame", "mime": "image/png", "encoding": "base64", "data": "YQ=="},
])
async def test_malformed_and_failure_release_waiter(bad):
    adapter = PhotoCapture(timeout=1)

    async def sender(message):
        if message["type"] == "system":
            adapter.note_device_event({**event(message["nonce"], ""), **bad})
        return True

    adapter.bind(sender, asyncio.get_running_loop())
    with pytest.raises(OSError):
        await run_capture(adapter)
    assert not adapter._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "disconnect", "replace", "send_failed"])
async def test_connection_failures_and_timeout(mode):
    adapter = PhotoCapture(timeout=0.05)

    async def sender(message):
        if message["type"] == "system":
            if mode == "disconnect":
                adapter.unbind(sender)
            elif mode == "replace":
                adapter.bind(sender, asyncio.get_running_loop())
        return mode != "send_failed"

    adapter.bind(sender, asyncio.get_running_loop())
    with pytest.raises(OSError):
        await run_capture(adapter)
    assert not adapter._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["duplicate", "out_of_order", "incomplete", "oversized", "bad_base64"])
async def test_chunk_state_validation(mode):
    adapter = PhotoCapture(timeout=1)

    async def sender(message):
        if message["type"] == "system":
            n = message["nonce"]
            adapter.note_device_event(event(n, "uploading", bytes=4, chunks=2,
                                            mime="image/jpeg", encoding="base64"))
            if mode == "out_of_order":
                adapter.note_device_event(event(n, "chunk", i=1, data="/9g="))
            elif mode == "oversized":
                adapter.note_device_event(event(n, "chunk", i=0, data=base64.b64encode(JPEG).decode()))
            elif mode == "bad_base64":
                adapter.note_device_event(event(n, "chunk", i=0, data="%%%%"))
            else:
                adapter.note_device_event(event(n, "chunk", i=0, data="/9g="))
                if mode == "duplicate":
                    adapter.note_device_event(event(n, "chunk", i=0, data="/9k="))
                else:
                    adapter.note_device_event(event(n, "complete"))
        return True

    adapter.bind(sender, asyncio.get_running_loop())
    with pytest.raises(OSError):
        await run_capture(adapter)


def test_no_connection():
    with pytest.raises(OSError):
        PhotoCapture().capture()


@pytest.mark.asyncio
async def test_concurrent_capture_is_rejected():
    adapter = PhotoCapture(timeout=1)
    requested = asyncio.Event()
    nonce = []

    async def sender(message):
        if message["type"] == "system":
            nonce.append(message["nonce"])
            requested.set()
        return True

    adapter.bind(sender, asyncio.get_running_loop())
    first = asyncio.create_task(run_capture(adapter))
    await requested.wait()
    with pytest.raises(OSError):
        await run_capture(adapter)
    adapter.note_device_event(event(nonce[0], "frame", mime="image/jpeg", encoding="base64",
                                    data=base64.b64encode(JPEG).decode()))
    assert await first == JPEG


@pytest.mark.asyncio
async def test_capture_cancel_releases_waiter():
    adapter = PhotoCapture(timeout=1)
    requested = asyncio.Event()

    async def sender(message):
        if message["type"] == "system":
            requested.set()
        return True

    adapter.bind(sender, asyncio.get_running_loop())
    capture = asyncio.create_task(run_capture(adapter))
    await requested.wait()
    adapter.cancel()
    with pytest.raises(OSError):
        await capture
    assert not adapter._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_homework_async_capture_keeps_state_on_loop(tmp_path, monkeypatch, cancel):
    from stackchan_mcp.homework_photo import HomeworkPhoto
    from stackchan_mcp.camera_presence import CameraPresence
    import threading

    loop_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()

    def capture():
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(1)
        return JPEG

    async def worker(fn):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return await asyncio.get_running_loop().run_in_executor(pool, fn)

    monkeypatch.setattr(asyncio, "to_thread", worker)
    photo = HomeworkPhoto(forms=object(), presence=CameraPresence(), capture=capture,
                          assess=lambda _: "blank", captures_dir=tmp_path)
    photo.begin({"id": 1, "title": "worksheet"})
    pending = asyncio.create_task(photo.attempt_capture_async())
    while not started.is_set():
        await asyncio.sleep(0.005)
    assert (await photo.attempt_capture_async())["error"] == "capture_pending"
    if cancel:
        photo.cancel()
    release.set()
    result = await pending
    if cancel:
        assert result["cancelled"]
        assert not list(tmp_path.glob("*.jpg"))
    else:
        assert result["photo_pending"] and result["quality"] == "blank"
        assert len(list(tmp_path.glob("*.jpg"))) == 1
