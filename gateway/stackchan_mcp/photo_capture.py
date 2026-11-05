# SPDX-License-Identifier: MIT
"""Bounded explicit JPEG requests over the existing device WS text connection.

No HTTP endpoint and no cloud forwarding. Capture runs in a worker thread;
receive events and the device sender remain on the gateway event loop.
"""
import asyncio
import base64
import binascii
from dataclasses import dataclass, field
import secrets
import threading
import time

MAX_BYTES = 1024 * 1024
MAX_CHUNKS = 512


@dataclass
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    frame: bytearray = field(default_factory=bytearray)
    error: str = ""
    size: int = 0
    chunks: int = 0
    index: int = 0


class PhotoCapture:
    def __init__(self, timeout=18.0):
        self.timeout = min(20.0, max(0.01, timeout))
        self._lock = threading.Lock()
        self._pending = {}
        self._sender = None
        self._loop = None

    def bind(self, sender, loop):
        with self._lock:
            self._fail_all("device connection changed")
            self._sender, self._loop = sender, loop

    def unbind(self, sender):
        with self._lock:
            if self._sender == sender:
                self._fail_all("device disconnected")
                self._sender = self._loop = None

    def cancel(self):
        with self._lock:
            self._fail_all("capture cancelled")

    def _fail_all(self, reason):
        for pending in self._pending.values():
            pending.error = reason
            pending.frame.clear()
            pending.done.set()

    def capture(self):
        """Synchronous HomeworkPhoto.capture contract; worker thread only."""
        with self._lock:
            sender, loop = self._sender, self._loop
            if sender is None or loop is None or not loop.is_running():
                raise OSError("camera device unavailable")
            try:
                if asyncio.get_running_loop() is loop:
                    raise OSError("capture requires a worker thread")
            except RuntimeError:
                pass
            if self._pending:
                raise OSError("camera capture already pending")
            nonce = secrets.token_urlsafe(24)
            pending = _Pending()
            self._pending[nonce] = pending
        deadline = time.monotonic() + self.timeout

        async def request():
            # UI only; the nonce-bearing system command is capture authority.
            if not await sender({"type": "xiaoya", "cmd": "homework_photo", "phase": "shutter",
                                 "sfx": "sfx.shutter", "face": "face.camera"}):
                raise OSError("camera device unavailable")
            if not await sender({"type": "system", "command": "xiaoya.photo", "nonce": nonce}):
                raise OSError("camera device unavailable")

        future = None
        try:
            future = asyncio.run_coroutine_threadsafe(request(), loop)
            future.result(timeout=max(0.001, deadline - time.monotonic()))
            if not pending.done.wait(max(0, deadline - time.monotonic())):
                raise OSError("camera capture timeout")
            with self._lock:
                if pending.error:
                    raise OSError(pending.error)
                return bytes(pending.frame)
        except Exception as exc:
            if future is not None:
                future.cancel()
            raise OSError("camera capture failed") from exc
        finally:
            with self._lock:
                self._pending.pop(nonce, None)
                pending.frame.clear()

    @staticmethod
    def _decode(data, remaining):
        if not isinstance(data, str) or not data or len(data) > 4 * ((remaining + 2) // 3):
            raise ValueError("invalid photo size")
        decoded = base64.b64decode(data, validate=True)
        if len(decoded) > remaining:
            raise ValueError("oversized photo")
        return decoded

    @staticmethod
    def _jpeg(frame):
        if not (frame.startswith(b"\xff\xd8") and frame.endswith(b"\xff\xd9")):
            raise ValueError("invalid JPEG")

    def note_device_event(self, message):
        if message.get("event") != "homework_photo":
            return
        nonce = message.get("nonce")
        if not isinstance(nonce, str):
            return
        with self._lock:
            p = self._pending.get(nonce)
            if p is None or p.done.is_set():
                return
            try:
                status = message.get("status")
                if status in {"camera_unavailable", "capture_failed", "too_large"}:
                    raise ValueError("device capture failed")
                if status == "frame":
                    if p.chunks or message.get("mime") != "image/jpeg" or message.get("encoding") != "base64":
                        raise ValueError("invalid frame")
                    p.frame.extend(self._decode(message.get("data"), MAX_BYTES))
                    self._jpeg(p.frame)
                    p.done.set()
                elif status == "uploading":
                    size, chunks = message.get("bytes"), message.get("chunks")
                    if (p.chunks or type(size) is not int or not 4 <= size <= MAX_BYTES or
                            type(chunks) is not int or not 1 <= chunks <= min(MAX_CHUNKS, size) or
                            message.get("mime") != "image/jpeg" or message.get("encoding") != "base64"):
                        raise ValueError("invalid upload")
                    p.size, p.chunks = size, chunks
                elif status == "chunk":
                    index = message.get("i")
                    if not p.chunks or type(index) is not int or index != p.index or index >= p.chunks:
                        raise ValueError("invalid chunk sequence")
                    p.frame.extend(self._decode(message.get("data"), p.size - len(p.frame)))
                    p.index += 1
                elif status == "complete":
                    if not p.chunks or p.index != p.chunks or len(p.frame) != p.size:
                        raise ValueError("incomplete photo")
                    self._jpeg(p.frame)
                    p.done.set()
                else:
                    raise ValueError("invalid photo status")
            except (ValueError, TypeError, binascii.Error):
                p.error = "invalid camera upload"
                p.frame.clear()
                p.done.set()


_capture = PhotoCapture()


def get_photo_capture():
    return _capture
