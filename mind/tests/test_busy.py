"""Masterの手元が忙しいか（core/chores/busy.py）。設計書 §2.4、海で暮らす §2.5。

守るもの：重い作業（ゲームなど）の間は裏方が動かない。その一方で、測れないときや一瞬の山で、裏方がずっと止まらない。
本人の脳（Ollama）・精神自身・Niraiの窓の負荷は、忙しさに数えない（数えると、話している間や海を見ている間ずっと忙しくなる）。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores import busy as busy_module
from mind.core.chores.busy import Busy, BusyRule, Reading, WindowsSense

T0 = datetime(2026, 10, 7, 21, 0, tzinfo=timezone.utc)
RULE = BusyRule(every_seconds=20, window_seconds=180, quiet_after_seconds=300, gpu_percent=40, cpu_percent=50)
GAME = 4242  # Niraiの外のプログラムの pid


class _Hands:
    """手元の替え玉。reading を差し替えると、次の測りがそれになる。"""

    def __init__(self) -> None:
        self.reading = Reading()
        self.reads = 0
        self.broken = False

    def sense(self) -> Reading:
        self.reads += 1
        if self.broken:
            raise OSError("カウンターが読めない")
        return self.reading


def _run(busy: Busy, start: datetime, seconds: int, step: int = 20) -> list[bool]:
    return [busy.busy(start + timedelta(seconds=s)) for s in range(0, seconds + 1, step)]


def test_a_short_spike_is_not_busy_but_sustained_use_is() -> None:
    hands = _Hands()
    busy = Busy(RULE, sense=hands.sense)
    assert not busy.busy(T0)
    hands.reading = Reading(gpu={GAME: 100.0})
    assert not busy.busy(T0 + timedelta(seconds=20))  # 20秒の山は、ならすと11%
    hands.reading = Reading()
    assert not any(_run(busy, T0 + timedelta(seconds=40), 260))

    hands.reading = Reading(gpu={GAME: 60.0})  # 60%を使い続ける：ならして40%に届くのは2分たってから
    seen = _run(busy, T0 + timedelta(seconds=320), 180)
    assert not any(seen[:5]) and all(seen[5:])


def test_busy_stays_a_while_after_the_load_ends_then_clears() -> None:
    hands = _Hands()
    busy = Busy(RULE, sense=hands.sense)
    hands.reading = Reading(cpu={GAME: 90.0})
    assert _run(busy, T0, 180)[-1]
    hands.reading = Reading()
    seen = _run(busy, T0 + timedelta(seconds=200), 400)
    # ならした値は負荷が消えてから下がり、最後に50%に届いているのは T0+260。そこから待つ長さ（300秒）は忙しいまま
    quiet = seen.index(False)
    assert 200 + 20 * quiet == 260 + 300
    assert all(seen[:quiet]) and not any(seen[quiet:])


def test_fullscreen_is_busy_at_once() -> None:
    hands = _Hands()
    busy = Busy(RULE, sense=hands.sense)
    assert not busy.busy(T0)
    hands.reading = Reading(fullscreen=True)
    assert busy.busy(T0 + timedelta(seconds=20))
    hands.reading = Reading()
    assert busy.busy(T0 + timedelta(seconds=300))
    assert not busy.busy(T0 + timedelta(seconds=340))


def test_light_use_by_many_programs_is_not_busy() -> None:
    """しきい値は1つのプログラムごと。小さい負荷がたくさんあっても、忙しくない。"""
    hands = _Hands()
    busy = Busy(RULE, sense=hands.sense)
    hands.reading = Reading(gpu={pid: 15.0 for pid in range(10)}, cpu={pid: 20.0 for pid in range(10)})
    assert not any(_run(busy, T0, 600))


def test_when_nothing_can_be_measured_it_is_not_busy_and_busy_does_not_stick() -> None:
    hands = _Hands()
    busy = Busy(RULE, sense=hands.sense)
    hands.broken = True
    assert not any(_run(busy, T0, 600))
    hands.broken = False
    hands.reading = Reading(fullscreen=True)
    assert busy.busy(T0 + timedelta(seconds=620))
    hands.broken = True  # 忙しい途中で測れなくなっても、待つ長さのあとは忙しくない
    assert not busy.busy(T0 + timedelta(seconds=620 + 320))


def test_it_measures_at_most_once_per_interval() -> None:
    hands = _Hands()
    busy = Busy(RULE, sense=hands.sense)
    for s in (0, 1, 5, 19):
        busy.busy(T0 + timedelta(seconds=s))
    assert hands.reads == 1
    busy.busy(T0 + timedelta(seconds=20))
    assert hands.reads == 2


# --- このPCで測る ---------------------------------------------------------------------------------------


def _process(name: str, ppid: int, user: float = 0.0) -> dict:
    return {"name": name, "ppid": ppid, "cpu_times": SimpleNamespace(user=user, system=0.0)}


@pytest.fixture
def machine(monkeypatch):  # noqa: ANN001, ANN201
    """プロセスの替え玉。Niraiの窓は 300（起動引数にアプリ窓の印）、Masterのいつものブラウザは 400。"""
    own = os.getpid()
    processes = {
        0: _process("System Idle Process", 0),
        own: _process("python.exe", 1),
        100: _process("ollama app.exe", 1),
        101: _process("ollama.exe", 100),
        102: _process("ollama.exe", 101),  # 脳を動かしている子（runner）
        300: _process("chrome.exe", 1),
        301: _process("chrome.exe", 300),  # 窓の描画（gpu-process）
        400: _process("chrome.exe", 1),
        401: _process("chrome.exe", 400),
        GAME: _process("game.exe", 1),
        500: _process("node.exe", own),  # 精神から起こしたほかのプログラムは、精神自身ではない
    }
    cmdlines = {
        300: ["chrome.exe", "--app=http://127.0.0.1:47810/", "--user-data-dir=C:\\Users\\m\\AppData\\Local\\Nirai\\window"],
        400: ["chrome.exe"],
    }

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def cmdline(self) -> list[str]:
            return cmdlines[self.pid]

    import psutil

    monkeypatch.setattr(psutil, "Process", _Process)
    monkeypatch.setattr(psutil, "cpu_count", lambda: 4)
    return SimpleNamespace(processes=processes, own=own)


def test_nirai_itself_is_not_counted(machine) -> None:  # noqa: ANN001
    nirai = WindowsSense()._nirai(machine.processes)
    assert nirai == {machine.own, 100, 101, 102, 300, 301}  # Masterのブラウザ（400・401）・ゲーム・500 は数える


def test_cpu_is_a_share_of_the_whole_pc_without_the_idle_time(machine, monkeypatch) -> None:  # noqa: ANN001
    clock = iter([1000.0, 1010.0])
    monkeypatch.setattr(busy_module.time, "monotonic", lambda: next(clock))
    sense = WindowsSense()
    assert sense._cpu(machine.processes) == {}  # 1回目は差がない
    later = dict(machine.processes)
    later[GAME] = _process("game.exe", 1, user=20.0)  # 10秒で20秒分＝4コアのうち半分
    later[0] = _process("System Idle Process", 0, user=20.0)
    usage = sense._cpu(later)
    assert usage[GAME] == pytest.approx(50.0)
    assert 0 not in usage


def test_gpu_is_the_busiest_engine_of_each_program(monkeypatch) -> None:  # noqa: ANN001
    clock = iter([1000.0, 1010.0])
    monkeypatch.setattr(busy_module.time, "monotonic", lambda: next(clock))
    engines = iter([
        {"pid_4242_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 0, "pid_4242_luid_0x0_0x1_phys_0_eng_4_engtype_Copy": 0},
        {"pid_4242_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 80_000_000, "pid_4242_luid_0x0_0x1_phys_0_eng_4_engtype_Copy": 10_000_000,
         "pid_77_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 50_000_000},  # 77 は測りの間に始まった（次から数える）
    ])
    monkeypatch.setattr(busy_module, "_gpu_running_time", lambda: next(engines))
    sense = WindowsSense()
    assert sense._gpu() == {}
    assert sense._gpu() == {GAME: pytest.approx(80.0)}


def test_a_signal_that_cannot_be_read_is_quiet_and_the_others_still_count(machine, monkeypatch) -> None:  # noqa: ANN001
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: [SimpleNamespace(pid=p, info=i) for p, i in machine.processes.items()])

    def broken() -> bool:
        raise OSError("SHQueryUserNotificationState: 0x80004005")

    monkeypatch.setattr(busy_module, "_fullscreen", broken)
    monkeypatch.setattr(busy_module, "_gpu_running_time", lambda: {"pid_301_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 0})
    reading = WindowsSense().read()
    assert reading.fullscreen is False and reading.gpu == {} and reading.cpu == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Windowsのカウンターを読む")
def test_this_pc_can_be_read() -> None:
    """本物のカウンター（全画面の印・GPU Engine・プロセスごとのCPU）が読める。中身はこのPCの今の様子なので見ない。"""
    sense = WindowsSense()
    sense.read()
    reading = sense.read()
    assert isinstance(reading.fullscreen, bool)
    assert sense._warned == set()  # どの信号も測れた
    assert os.getpid() not in reading.cpu and os.getpid() not in reading.gpu
