"""Local motion-generator HTTP boundary.

The actual Kimodo backend and resource checks are connected in the next D0
step. The boundary can be verified with a disposable fake without loading any
model, reaching the network or touching a resident's idea.
"""
from __future__ import annotations

import json
import math
import secrets
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY_BYTES = 2048
SEED_LIMIT = 2**31 - 1


def motion_request(raw: bytes) -> tuple[str, float, int]:
    """Validate public input; never include the submitted text in errors."""
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON") from None
    if not isinstance(request, dict) or set(request) - {"text", "seconds", "seed"}:
        raise ValueError("Invalid motion arguments")
    message = request.get("text")
    if not isinstance(message, str) or not 1 <= len(message) <= 200 or not message.strip():
        raise ValueError("Invalid text length")
    seconds = request.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 10:
        raise ValueError("Invalid duration")
    seed = request.get("seed", None)
    if seed is None:
        seed = secrets.randbelow(SEED_LIMIT + 1)
    if isinstance(seed, bool) or not isinstance(seed, int) or not -SEED_LIMIT <= seed <= SEED_LIMIT:
        raise ValueError("Invalid seed")
    return message, float(seconds), seed


def valid_glb(data: object) -> bool:
    return (isinstance(data, bytes) and len(data) >= 12 and data[:4] == b"glTF"
            and struct.unpack_from("<II", data, 4) == (2, len(data)))


class MotionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int = 0):
        # Always bind to loopback. There is intentionally no configurable host.
        super().__init__(("127.0.0.1", port), MotionHandler)
        self.generator = None
        self.generate_lock = threading.Lock()

    def install(self, generator) -> None:
        """Only publish a backend when loading and checks have finished."""
        self.generator = generator

    def handle_error(self, request, client_address):
        # BaseServer otherwise writes the exception and its arguments to stderr.
        # Some backends may put a submitted text in an exception.
        pass


class MotionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Never log URL, request bodies or generator errors.
        pass

    def reply(self, status: int, data: bytes = b"", content_type: str = "application/json", seed: int | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if seed is not None:
            self.send_header("X-Motion-Seed", str(seed))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        if self.path != "/health":
            self.reply(404)
            return
        ready = self.server.generator is not None
        self.reply(200 if ready else 503, b'{"ready":true}' if ready else b'{"ready":false}')

    def do_POST(self):
        if self.path != "/motion":
            self.reply(404)
            return
        try:
            size = int(self.headers.get("Content-Length", ""))
            if not 1 <= size <= MAX_BODY_BYTES:
                raise ValueError("Invalid request size")
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError("Expected JSON")
            raw = self.rfile.read(size)
            text, seconds, seed = motion_request(raw)
        except (ValueError, OverflowError):
            self.close_connection = True
            self.reply(400)
            return
        generator = self.server.generator
        if generator is None:
            self.reply(503)
            return
        if not self.server.generate_lock.acquire(blocking=False):
            self.reply(503)
            return
        try:
            try:
                motion = generator.generate(text, seconds, seed)
                if not valid_glb(motion):
                    raise ValueError("Invalid GLB output")
            except Exception:
                self.reply(500)
                return
            self.reply(200, motion, "model/gltf-binary", seed)
        finally:
            self.server.generate_lock.release()
