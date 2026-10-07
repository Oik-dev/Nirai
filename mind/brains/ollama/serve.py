"""Ollama への入口。モデルを載せてから頼む。Ollama が動いていなければ（つながらなければ）起こして、もう1回だけ送る。

脳への呼び出しはすべてここを通る（brains/ollama/adapter.py・ask_json.py、core/memory/embedder.py）。精神は海から
ひとりで起こされるので、Ollama を起こすのも脳への入口の仕事（前は Serina.bat が起動の前に起こしていた）。
起こすのは呼び出しが断られたときだけで、呼び出しごとに1回まで（昼に Ollama が落ちても、次の呼び出しで戻る）。
同時に断られた呼び出しは、1つの起動を待つ。`ollama serve` は切り離した子として起こす（精神が起こし直されても Ollama は
動き続け、モデルを読み直さない）。出力は捨てる（精神の記録のファイルを握らせない）。

モデルは載せたまま（keep_alive -1）。下ろすのは、Masterの手元が忙しい間の見回りだけ（unload）。頼む前には毎回、同じ入口へ
空の頼みを送って載せる（Ollama は空の頼みでは読み込むだけをする。載っていれば一瞬で返る）。読み込みは、HDD から冷えた
Gemma を読むと4分を超えるので、頼みそのものの待ち（返事の無応答・埋め込み）と分けて長く待つ。分けないと、読み込みの間に
思い出す・返事するの待ちが切れる（2026-10-06・07、起きた直後の最初のターンで実際に切れていた）。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)
T = TypeVar("T")

STARTUP_WAIT_SECONDS = 30.0  # Serina.bat と同じ待ち
KEEP_LOADED = -1
LOAD_WAIT_SECONDS = 900.0  # 冷えた Gemma の読み込みは D: の HDD から 4分20秒（2026-10-06 の Ollama の記録）
_starting = threading.Lock()


def post(url: str, *, json: dict, **kwargs: Any) -> requests.Response:
    """requests.post と同じ。送る前にモデルを載せる。Ollama につながらなければ、動くようにしてから、もう1回だけ送る。"""
    payload = {**json, "keep_alive": KEEP_LOADED}

    def send() -> requests.Response:
        load(url, payload)
        return requests.post(url, json=payload, **kwargs)

    return _reaching_ollama(url, send)


def warm(url: str, payload: dict) -> None:
    """payload のモデルを載せておく（Ollama が止まっていれば起こす）。頼みが来たときに読み込みを待たせないため。"""
    _reaching_ollama(url, lambda: load(url, payload))


def load(url: str, payload: dict) -> None:
    """同じ入口へ空の頼みを送って、payload のモデル（options も同じ。違うと読み込み直しになる）を載せる。"""
    if not payload.get("model"):
        return
    requests.post(
        url,
        json={
            "model": payload["model"],
            "prompt": "",
            "stream": False,
            "keep_alive": KEEP_LOADED,
            "options": payload.get("options") or {},
        },
        timeout=LOAD_WAIT_SECONDS,
    ).raise_for_status()


def _reaching_ollama(url: str, call: Callable[[], T]) -> T:
    try:
        return call()
    except requests.ConnectionError:
        parts = urlsplit(url)
        if not ensure_running(f"{parts.scheme}://{parts.netloc}"):
            raise
    return call()


def unload(base_url: str, model: str) -> bool:
    """Ollama に載っている model を下ろす（keep_alive: 0）。下ろしたら True。次に呼ばれたら、Ollama がまた載せる。

    載っていなければ何もしない（下ろす頼みで読み込ませない）。Ollama が動いていなければ、起こさない。
    """
    tagged = model if ":" in model else f"{model}:latest"
    try:
        loaded = requests.get(f"{base_url}/api/ps", timeout=5).json()
        models = loaded.get("models") if isinstance(loaded, dict) else None
        if not any(isinstance(m, dict) and tagged in (m.get("name"), m.get("model")) for m in models or ()):
            return False
        requests.post(f"{base_url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30).raise_for_status()
    except (requests.RequestException, ValueError):
        return False
    return True


def ensure_running(base_url: str) -> bool:
    """Ollama が応えるようにする。応えていなければ `ollama serve` を起こして、応えるまで待つ。応えたら True。"""
    with _starting:
        if _responds(base_url):
            return True
        if not _start():
            return False
        deadline = time.monotonic() + STARTUP_WAIT_SECONDS
        while time.monotonic() < deadline:
            if _responds(base_url):
                logger.info("Ollama を起こした")
                return True
            time.sleep(0.5)
    logger.error("Ollama を起こしたが、%d秒たっても応えない", STARTUP_WAIT_SECONDS)
    return False


def _responds(base_url: str) -> bool:
    try:
        return requests.get(f"{base_url}/api/version", timeout=2).ok
    except requests.RequestException:
        return False


def _start() -> bool:
    exe = shutil.which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    if not Path(exe).is_file():
        logger.error("Ollama が見つからない（%s）", exe)
        return False
    logger.info("Ollama が動いていないので起こす（%s serve）", exe)
    subprocess.Popen(
        [exe, "serve"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return True
