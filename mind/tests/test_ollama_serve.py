"""脳への入口（brains/ollama/serve.py）：Ollama が動いていなければ起こして、もう1回だけ送る。

守るもの：精神は海からひとりで起こされるので、Ollama が止まっていても最初の呼び出しで起こせる。起こすのは呼び出しごとに1回まで
（起こしても応えなければ、断られたまま返す。何度も起こし続けない）。同時に断られた呼び出しは、1つの起動を待つ（2つ起こさない）。
本物の Ollama は起こさない（起こす関数と応える関数を替え玉にする）。
脳を下ろす（Masterの手元が忙しい間）のは、載っているときだけで、下ろす頼みで読み込ませず、止まっている Ollama を起こさない。
下ろしたあとも、次の呼び出しで Ollama がまた載せる（下ろしたことを精神が覚えて、呼び出しを止めない）。
頼む前に、空の頼みでモデルを載せる。読み込みの待ちは長く（HDD から冷えた脳を読むと4分を超える）、頼みの待ちは頼みのまま。
これがないと、起きた直後の最初のターンで、思い出す（30秒）・返事（240秒）の待ちが読み込みの途中で切れる。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama import serve

URL = "http://localhost:11434/api/generate"


@pytest.fixture
def ollama(monkeypatch):  # noqa: ANN001, ANN201
    """替え玉の Ollama。up のときだけ応える。start で起きる（starts_up=False なら起きない）。"""
    world = {"up": False, "starts": 0, "posts": 0, "starts_up": True, "fail_even_up": False}

    class _Ok(str):
        def raise_for_status(self) -> None:
            pass

    def fake_post(url, **_kwargs):  # noqa: ANN001, ANN003, ANN202
        world["posts"] += 1
        if not world["up"] or world["fail_even_up"]:
            raise requests.ConnectionError("refused")
        return _Ok(f"ok {url}")

    def fake_start() -> bool:
        world["starts"] += 1
        world["up"] = world["starts_up"]
        return True

    monkeypatch.setattr(serve.requests, "post", fake_post)
    monkeypatch.setattr(serve, "_responds", lambda base: world["up"] and base == "http://localhost:11434")
    monkeypatch.setattr(serve, "_start", fake_start)
    monkeypatch.setattr(serve, "STARTUP_WAIT_SECONDS", 0.2)
    return world


def test_a_refused_call_starts_ollama_once_and_sends_again(ollama) -> None:  # noqa: ANN001
    assert serve.post(URL, json={}) == f"ok {URL}"
    assert (ollama["starts"], ollama["posts"]) == (1, 2)
    assert serve.post(URL, json={}) == f"ok {URL}"
    assert (ollama["starts"], ollama["posts"]) == (1, 3)  # 動いていれば、もう起こさない


def test_when_ollama_does_not_come_up_the_call_is_refused(ollama) -> None:  # noqa: ANN001
    ollama["starts_up"] = False
    with pytest.raises(requests.ConnectionError):
        serve.post(URL, json={})
    assert (ollama["starts"], ollama["posts"]) == (1, 1)


def test_a_retry_that_is_refused_again_is_not_retried_more(ollama) -> None:  # noqa: ANN001
    ollama["fail_even_up"] = True
    with pytest.raises(requests.ConnectionError):
        serve.post(URL, json={})
    assert (ollama["starts"], ollama["posts"]) == (1, 2)


def test_when_ollama_cannot_be_found_the_call_is_refused(ollama, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(serve, "_start", lambda: False)
    with pytest.raises(requests.ConnectionError):
        serve.post(URL, json={})
    assert ollama["posts"] == 1


def test_calls_refused_together_wait_for_one_start(ollama, monkeypatch) -> None:  # noqa: ANN001
    both_refused = threading.Barrier(2)
    real_post = serve.requests.post

    def post_together(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
        try:
            return real_post(url, **kwargs)
        except requests.ConnectionError:
            both_refused.wait(timeout=2)  # 2つとも断られてから、起こしに行く
            raise

    monkeypatch.setattr(serve.requests, "post", post_together)
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(serve.post(URL, json={}))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert results == [f"ok {URL}"] * 2
    assert ollama["starts"] == 1


# --- 脳を下ろす ---------------------------------------------------------------------------------


@pytest.fixture
def loaded_ollama():  # noqa: ANN201
    """HTTPで応える替え玉の Ollama。/api/ps は載っているモデル、/api/generate は keep_alive 0 で下ろし、ほかは載せて答える。"""
    world: dict = {"loaded": [], "generates": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # noqa: ANN002
            pass

        def _send(self, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._send({"models": [{"name": name, "model": name} for name in world["loaded"]]})

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            world["generates"].append(body)
            name = body["model"] if ":" in body["model"] else body["model"] + ":latest"
            if body.get("keep_alive") == 0:
                world["loaded"] = [m for m in world["loaded"] if m != name]
                self._send({"model": body["model"], "done": True, "done_reason": "unload"})
                return
            world["loaded"] = sorted({*world["loaded"], name})
            self._send({"model": body["model"], "response": "うん", "done": True})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    world["url"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield world
    httpd.shutdown()
    httpd.server_close()


def test_unload_asks_only_when_the_brain_is_loaded(loaded_ollama) -> None:  # noqa: ANN001
    assert serve.unload(loaded_ollama["url"], "serina-gemma4-unc") is False
    assert loaded_ollama["generates"] == []  # 載っていなければ頼まない（頼むと読み込ませてしまう）
    loaded_ollama["loaded"] = ["bge-m3:latest", "serina-gemma4-unc:latest"]
    assert serve.unload(loaded_ollama["url"], "serina-gemma4-unc") is True
    assert loaded_ollama["generates"] == [{"model": "serina-gemma4-unc", "keep_alive": 0}]
    assert loaded_ollama["loaded"] == ["bge-m3:latest"]  # 下ろすのは本人の脳だけ


def test_after_resting_the_next_call_loads_the_brain_again(loaded_ollama) -> None:  # noqa: ANN001
    from mind.brains.ollama.adapter import OllamaAdapter

    loaded_ollama["loaded"] = ["serina-gemma4-unc:latest"]
    assert serve.unload(loaded_ollama["url"], "serina-gemma4-unc")
    assert OllamaAdapter(base_url=loaded_ollama["url"]).raw_call("ただいま") == "うん"
    load, call = loaded_ollama["generates"][-2:]
    assert (load["prompt"], call["prompt"]) == ("", "ただいま")  # 載せてから頼む
    assert load["options"] == call["options"]  # 違うと、頼んだときに読み込み直しになる
    assert loaded_ollama["loaded"] == ["serina-gemma4-unc:latest"]


# --- 載せてから頼む・載せたまま ------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):  # noqa: ANN001, ANN201
    """requests.post の替え玉。送った (url, json, timeout) を順に持つ。本物の Ollama には届かない。"""
    calls: list[tuple[str, dict, float]] = []

    class _Response:
        def raise_for_status(self) -> None:
            pass

    def fake_post(url, *, json, timeout, **_kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append((url, json, timeout))
        return _Response()

    monkeypatch.setattr(serve.requests, "post", fake_post)
    return calls


def test_a_call_first_loads_its_model_waiting_long_then_sends_with_its_own_wait(sent) -> None:  # noqa: ANN001
    payload = {"model": "serina-gemma4-unc", "prompt": "ただいま", "stream": True, "options": {"num_ctx": 8192, "use_mmap": True}}
    serve.post(URL, json=payload, timeout=240, stream=True)
    (load_url, load, load_wait), (call_url, call, call_wait) = sent
    assert load_url == call_url == URL
    assert load == {"model": "serina-gemma4-unc", "prompt": "", "stream": False, "keep_alive": -1, "options": payload["options"]}
    assert load_wait == serve.LOAD_WAIT_SECONDS > 4 * 60 + 20  # HDD から冷えた脳を読む時間より長く待つ
    assert call == {**payload, "keep_alive": -1} and call_wait == 240  # 頼みは載せたままで、待ちは頼みのもの


def test_warming_loads_without_asking_anything(sent) -> None:  # noqa: ANN001
    from mind.brains.ollama.adapter import OllamaAdapter
    from mind.core.memory.embedder import OllamaEmbedder

    OllamaAdapter(num_ctx=8192, use_mmap=True).warm()
    OllamaEmbedder().warm()
    assert [(url.rsplit("/", 1)[-1], body["model"], body["prompt"], body["options"]) for url, body, _ in sent] == [
        ("generate", "serina-gemma4-unc", "", {"num_ctx": 8192, "use_mmap": True}),
        ("embeddings", "bge-m3", "", {"num_gpu": 0}),
    ]


def test_doubles_of_the_brain_are_not_warmed(sent) -> None:  # noqa: ANN001
    from mind.brains.ollama.adapter import OllamaAdapter
    from mind.core.memory.embedder import OllamaEmbedder

    OllamaAdapter(chat_call_fn=lambda _prompt: "うん").warm()
    OllamaEmbedder(call_fn=lambda _model, _text: [0.1]).warm()
    assert sent == []


def test_warming_starts_a_stopped_ollama(ollama) -> None:  # noqa: ANN001
    serve.warm(URL, {"model": "serina-gemma4-unc"})
    assert (ollama["starts"], ollama["posts"]) == (1, 2)


def test_unload_does_not_start_a_stopped_ollama(monkeypatch) -> None:  # noqa: ANN001
    starts: list[int] = []
    monkeypatch.setattr(serve, "_start", lambda: starts.append(1) or True)
    with socket.socket() as probe:  # 誰も聞いていないポート
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert serve.unload(f"http://127.0.0.1:{port}", "serina-gemma4-unc") is False
    assert starts == []
