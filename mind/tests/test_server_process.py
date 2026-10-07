"""精神のプロセス（app/server.py の main）：ひとりで起きて、ひとりで止まる。

守るもの：
- 精神への道（ポート）は世界の設定が持つ。NIRAI_MIND_PORT がなければ、イデアに触れずに終わる。
- イデアごとに精神は1つ。もう精神がロック（data/mind.lock）を持っていたら、2つ目はイデアのファイルを1つも書かずに終わる。
- POST /api/close で、返事を返してから本当に終わり、ロックも外れる（海の「起こし直す」が次の精神を起こせる）。
本物の精神をサブプロセスで起こす（使い捨てのイデア。脳は呼ばないうちに止める）。
"""

from __future__ import annotations

import msvcrt
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "app" / "server.py"


@pytest.fixture
def idea(tmp_path: Path) -> Path:
    root = tmp_path / "idea"
    root.mkdir()
    (root / "identity.toml").write_text('name = "テスト用の住人"\n', encoding="utf-8")
    shutil.copytree(Path(__file__).parent / "fixtures" / "persona", root / "persona")
    return root


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _mind(idea: Path, port: int | None) -> subprocess.Popen:
    env = {**os.environ, "NIRAI_IDEA": str(idea), "PYTHONUTF8": "1"}
    env.pop("NIRAI_MIND_PORT", None)
    if port is not None:
        env["NIRAI_MIND_PORT"] = str(port)
    return subprocess.Popen([sys.executable, str(SERVER)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _snapshot(root: Path) -> dict[str, float]:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*")}


def test_without_a_port_the_mind_does_not_start(idea: Path) -> None:
    before = _snapshot(idea)
    mind = _mind(idea, None)
    assert mind.wait(timeout=60) == 2
    assert _snapshot(idea) == before


def test_a_second_mind_for_the_same_idea_writes_nothing(idea: Path) -> None:
    lock = idea / "data" / "mind.lock"
    lock.parent.mkdir()
    with open(lock, "a+b") as held:  # 1つ目の精神がロックを持っている
        held.seek(0)
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
        before = _snapshot(idea)
        mind = _mind(idea, _free_port())
        assert mind.wait(timeout=60) == 3
        assert "もう起きています" in mind.stdout.read().decode("utf-8")
        assert _snapshot(idea) == before


def test_close_ends_the_mind_and_frees_the_lock(idea: Path) -> None:
    port = _free_port()
    mind = _mind(idea, port)
    try:
        for _ in range(600):
            try:
                if requests.get(f"http://127.0.0.1:{port}/api/state", timeout=1).ok:
                    break
            except requests.ConnectionError:
                pass
            assert mind.poll() is None, mind.stdout.read().decode("utf-8")
            time.sleep(0.1)
        response = requests.post(f"http://127.0.0.1:{port}/api/close", timeout=5)
        assert response.status_code == 200 and response.json() == {"ok": True}
        assert mind.wait(timeout=10) == 0
    finally:
        if mind.poll() is None:
            mind.kill()
    with open(idea / "data" / "mind.lock", "a+b") as again:  # ロックは外れている（次の精神が取れる）
        again.seek(0)
        msvcrt.locking(again.fileno(), msvcrt.LK_NBLCK, 1)
        msvcrt.locking(again.fileno(), msvcrt.LK_UNLCK, 1)
