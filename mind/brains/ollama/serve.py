"""Ollama への入口。Ollama が動いていなければ（つながらなければ）起こして、もう1回だけ送る。

脳への呼び出しはすべてここを通る（brains/ollama/adapter.py・ask_json.py、core/memory/embedder.py）。精神は海から
ひとりで起こされるので、Ollama を起こすのも脳への入口の仕事（前は Serina.bat が起動の前に起こしていた）。
起こすのは呼び出しが断られたときだけで、呼び出しごとに1回まで（昼に Ollama が落ちても、次の呼び出しで戻る）。
同時に断られた呼び出しは、1つの起動を待つ。`ollama serve` は切り離した子として起こす（精神が起こし直されても Ollama は
動き続け、モデルを読み直さない）。出力は捨てる（精神の記録のファイルを握らせない）。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

STARTUP_WAIT_SECONDS = 30.0  # Serina.bat と同じ待ち
_starting = threading.Lock()


def post(url: str, **kwargs: Any) -> requests.Response:
    """requests.post と同じ。Ollama につながらなければ、動くようにしてから、もう1回だけ送る。"""
    try:
        return requests.post(url, **kwargs)
    except requests.ConnectionError:
        parts = urlsplit(url)
        if not ensure_running(f"{parts.scheme}://{parts.netloc}"):
            raise
    return requests.post(url, **kwargs)


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
