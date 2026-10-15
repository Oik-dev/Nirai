"""会話に脳を譲る低優先度のHTTP呼び出し。本番Ollamaは一切使わない。"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from mind.brains.ollama import serve


def test_yield_before_load_does_not_send(monkeypatch):
    loads = []
    monkeypatch.setattr(serve, "load", lambda *_: loads.append(1))
    with serve.yielding(lambda: True):
        with pytest.raises(serve.Yielded):
            serve.post("http://127.0.0.1:1/api/generate", json={"model": "fake", "stream": False})
    assert not loads


def test_non_yielding_call_preserves_existing_requests_transport(monkeypatch):
    sent = []
    monkeypatch.setattr(serve, "load", lambda *_: None)
    monkeypatch.setattr(serve.requests, "post", lambda *a, **kw: sent.append((a, kw)) or "normal")
    assert serve.post("http://127.0.0.1:1/api/generate", json={"stream": False}) == "normal"
    assert len(sent) == 1


def test_yielded_does_not_restart_or_retry(monkeypatch):
    starts = []
    monkeypatch.setattr(serve, "load", lambda *_: None)
    monkeypatch.setattr(serve, "ensure_running", lambda *_: starts.append(1) or True)
    monkeypatch.setattr(serve, "_interruptible_post", lambda *a, **kw: (_ for _ in ()).throw(serve.Yielded()))
    with serve.yielding(lambda: False):
        with pytest.raises(serve.Yielded):
            serve.post("http://127.0.0.1:1/api/generate", json={"stream": False})
    assert starts == []


def test_connection_failure_still_restarts_once(monkeypatch):
    calls = []
    starts = []
    monkeypatch.setattr(serve, "load", lambda *_: None)
    monkeypatch.setattr(serve, "ensure_running", lambda *_: starts.append(1) or True)

    def send(*_a, **_kw):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("not running")
        return "recovered"

    monkeypatch.setattr(serve, "_interruptible_post", send)
    with serve.yielding(lambda: False):
        assert serve.post("http://127.0.0.1:1/api/generate", json={"stream": False}) == "recovered"
    assert len(calls) == 2 and len(starts) == 1


def test_waiting_nonstream_http_disconnects_within_one_second(monkeypatch):
    received = threading.Event()
    closed = threading.Event()
    stop = threading.Event()

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.set()
            self.connection.settimeout(4)
            try:
                if self.connection.recv(1) == b"":
                    closed.set()
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(serve, "load", lambda *_: None)
    result = []

    def worker():
        try:
            with serve.yielding(stop.is_set):
                serve.post(f"http://127.0.0.1:{server.server_port}/api/generate",
                           json={"model": "fake", "stream": False}, timeout=6)
        except serve.Yielded:
            result.append("yielded")

    client = threading.Thread(target=worker, daemon=True)
    try:
        client.start()
        assert received.wait(3), "the fake server did not receive the request"
        stop.set()
        client.join(1.5)
        assert not client.is_alive()
        assert result == ["yielded"]
        assert closed.wait(1.5), "the interrupted connection was not closed"
    finally:
        server.shutdown()
        server.server_close()


def test_yielding_scope_does_not_leak_across_calls(monkeypatch):
    monkeypatch.setattr(serve, "load", lambda *_: None)
    monkeypatch.setattr(serve.requests, "post", lambda *_a, **_kw: "ordinary")
    with serve.yielding(lambda: True):
        with pytest.raises(serve.Yielded):
            serve.post("http://127.0.0.1:1/api/generate", json={"stream": False})
    assert serve.post("http://127.0.0.1:1/api/generate", json={"stream": False}) == "ordinary"


def test_nonstream_success_has_requests_response_contract(monkeypatch):
    class OkHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            output = b'{"response":"ready"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), OkHandler)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    monkeypatch.setattr(serve, "load", lambda *_: None)
    try:
        with serve.yielding(lambda: False):
            result = serve.post(f"http://127.0.0.1:{server.server_port}/api/generate",
                                json={"stream": False}, timeout=4)
        result.raise_for_status()
        assert result.json() == {"response": "ready"}
        assert result.status_code == 200
    finally:
        server.shutdown()
        server.server_close()
