"""Masterの手元が忙しいか。設計書 §2.4、海で暮らす §2.5。重い作業（ゲームなど）と本人の裏方をかぶらせない。

忙しいとは、Windowsの「全画面の印」（SHQueryUserNotificationState が全画面・D3Dの全画面・プレゼン）か、Niraiの外の
1つのプログラムが、GPU（プログラムごとの GPU Engine カウンター）かCPUを、数分ならして使い続けていること。
忙しさが消えてからも、数分は忙しいままにする。

Niraiの中は数えない：本人の脳（Ollama）、精神自身、Niraiの窓（127.0.0.1 を開くChromeのアプリ窓とその子）。
窓を数えると、Masterが海を見ている間ずっと忙しくなり、Pulseも眠りも来なくなる。

測れない信号は「忙しくない」として扱う（測る仕組みの不調で、裏方がずっと止まらないため）。
見回りと眠りの区切りが busy(now) を呼び、前に測ってから間があいていれば測り直す（眠りは見回りの中で何分も
続くので、区切りでも測らないと、眠っている間に始まったゲームに気づけない）。
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# SHQueryUserNotificationState の答えのうち、Masterの手元が忙しい印
_QUNS_BUSY = 2  # 全画面のアプリ（枠なしの全画面のゲームもここ）
_QUNS_RUNNING_D3D_FULL_SCREEN = 3  # 排他の全画面（Direct3D）
_QUNS_PRESENTATION_MODE = 4  # プレゼンテーションの設定
_FULLSCREEN_STATES = frozenset({_QUNS_BUSY, _QUNS_RUNNING_D3D_FULL_SCREEN, _QUNS_PRESENTATION_MODE})

WINDOW_MARK = "--app=http://127.0.0.1:"  # Niraiの窓のChrome（world/window/open.ps1）の起動引数
WORKSHOP_MARK = "--nirai-workshop"


@dataclass(frozen=True)
class BusyRule:
    """忙しさの決まり（設定は config/app_timing.toml の [busy]）。"""

    every_seconds: float = 20  # 前に測ってから、これだけたっていれば測り直す（見回りの間隔）
    window_seconds: float = 180  # ならす長さ
    quiet_after_seconds: float = 300  # 忙しさが消えてから、忙しいままにする長さ
    gpu_percent: float = 40  # 1つのプログラムのGPU使用率（エンジンごとの最大）の、ならした値のしきい値
    cpu_percent: float = 50  # 1つのプログラムのCPU使用率（PC全体に対する割合）の、ならした値のしきい値


@dataclass(frozen=True)
class Reading:
    """1回の測り。gpu・cpu は、Niraiの外のプログラム（pid）ごとの、前の測りからの平均の使用率（%）。"""

    fullscreen: bool = False
    gpu: Mapping[int, float] = field(default_factory=dict)
    cpu: Mapping[int, float] = field(default_factory=dict)
    workshop: bool = False


class Busy:
    """忙しいかの判定（精神に1つ）。sense は1回の測り方（替え玉を入れられる）。"""

    def __init__(self, rule: BusyRule, sense: Callable[[], Reading] | None = None) -> None:
        self._rule = rule
        self._sense = sense or WindowsSense().read
        self._lock = threading.Lock()
        self._readings: deque[tuple[datetime, float, Reading]] = deque()  # (測った時刻, 何秒分の平均か, 測り)
        self._measured_at: datetime | None = None
        self._busy_at: datetime | None = None  # 最後に忙しいと見た時刻
        self._workshop_active = False  # 工房は余韻を持たず、印がある間だけ忙しい
        self._failing = False  # 測れないことを記録に残したか（続くあいだは1回だけ）

    def busy(self, now: datetime) -> bool:
        with self._lock:
            if self._measured_at is None or (now - self._measured_at).total_seconds() >= self._rule.every_seconds:
                self._measure(now)
            return self._workshop_active or self._master_busy(now)

    def master_busy(self, now: datetime) -> bool:
        """工房を除いたMaster自身の手元の忙しさ。/api/hands用。"""
        with self._lock:
            if self._measured_at is None or (now - self._measured_at).total_seconds() >= self._rule.every_seconds:
                self._measure(now)
            return self._master_busy(now)

    def _master_busy(self, now: datetime) -> bool:
        return self._busy_at is not None and (now - self._busy_at).total_seconds() < self._rule.quiet_after_seconds

    def _measure(self, now: datetime) -> None:
        covered = 0.0 if self._measured_at is None else (now - self._measured_at).total_seconds()
        self._measured_at = now
        try:
            reading = self._sense()
            self._failing = False
        except Exception:  # noqa: BLE001 — 測れなければ、忙しくない（WindowsSense は信号ごとに受け止めている）
            if not self._failing:
                logger.exception("忙しさを測れなかった（忙しくない扱いで続ける）")
            self._failing = True
            reading = Reading()
        self._workshop_active = reading.workshop
        self._readings.append((now, covered, reading))
        window = self._rule.window_seconds
        while self._readings and (now - self._readings[0][0]).total_seconds() > window:
            self._readings.popleft()
        if reading.fullscreen or self._loaded(now):
            self._busy_at = now

    def _loaded(self, now: datetime) -> bool:
        """Niraiの外のどれか1つのプログラムが、ならす長さ（window_seconds）の平均で、GPUかCPUのしきい値を超えたか。

        分母は、ならす長さで決まっている（測り始めの一瞬の山や、測れなかった間を、使い続けたと数えない）。
        """
        window = self._rule.window_seconds
        gpu: dict[int, float] = {}
        cpu: dict[int, float] = {}
        for at, covered, reading in self._readings:
            seconds = max(0.0, min(covered, window - (now - at).total_seconds()))
            for pid, percent in reading.gpu.items():
                gpu[pid] = gpu.get(pid, 0.0) + percent * seconds
            for pid, percent in reading.cpu.items():
                cpu[pid] = cpu.get(pid, 0.0) + percent * seconds
        return any(total / window >= self._rule.gpu_percent for total in gpu.values()) or any(
            total / window >= self._rule.cpu_percent for total in cpu.values()
        )


class WindowsSense:
    """このPCで測る。信号ごとに失敗を受け止め、測れない信号は「忙しくない」にする（最初の1回だけ記録に残す）。"""

    def __init__(self) -> None:
        self._own = os.getpid()
        self._gpu_before: tuple[float, dict[str, int]] | None = None  # (時刻, エンジンごとの動いていた時間[100ns])
        self._cpu_before: tuple[float, dict[int, float]] | None = None  # (時刻, プログラムごとのCPU時間[秒])
        self._warned: set[str] = set()

    def read(self) -> Reading:
        import psutil

        processes = {
            p.pid: p.info for p in psutil.process_iter(["name", "ppid", "cpu_times"], ad_value=None)
        }
        nirai = self._nirai(processes)
        workshop = self._signal("workshop", lambda: self._workshop(processes), set())
        nirai |= workshop
        return Reading(
            fullscreen=self._signal("fullscreen", _fullscreen, False),
            gpu=_outside(self._signal("gpu", self._gpu, {}), nirai),
            cpu=_outside(self._signal("cpu", lambda: self._cpu(processes), {}), nirai),
            workshop=bool(workshop),
        )

    def _workshop(self, processes: Mapping[int, dict]) -> set[int]:
        """起動引数の印から生成器とその子を見つける。プログラム名に頼らない。"""
        import psutil

        roots: set[int] = set()
        for pid in processes:
            try:
                if WORKSHOP_MARK in psutil.Process(pid).cmdline():
                    roots.add(pid)
            except (psutil.Error, OSError):
                continue
        return _with_descendants(roots, {pid: info.get("ppid") for pid, info in processes.items()})

    def _signal(self, name: str, measure: Callable, fallback):  # noqa: ANN001, ANN202
        try:
            return measure()
        except Exception as exc:  # noqa: BLE001
            if name not in self._warned:
                self._warned.add(name)
                logger.warning("忙しさの「%s」を測れない（忙しくない扱い）: %s", name, exc)
            return fallback

    def _nirai(self, processes: Mapping[int, dict]) -> set[int]:
        """Niraiの中のプログラム：本人の脳（Ollama）とその子、精神自身、Niraiの窓とその子。"""
        import psutil

        roots: set[int] = set()
        for pid, info in processes.items():
            name = (info.get("name") or "").lower()
            if name.startswith("ollama"):
                roots.add(pid)
            elif name == "chrome.exe" and (processes.get(info.get("ppid")) or {}).get("name", "").lower() != "chrome.exe":
                try:  # ブラウザ本体だけ見る（子の起動引数には、アプリ窓の印が載らない）
                    if any(arg.startswith(WINDOW_MARK) for arg in psutil.Process(pid).cmdline()):
                        roots.add(pid)
                except (psutil.Error, OSError):
                    continue
        return _with_descendants(roots, {pid: info.get("ppid") for pid, info in processes.items()}) | {self._own}

    def _gpu(self) -> dict[int, float]:
        now = time.monotonic()
        running = _gpu_running_time()
        before, self._gpu_before = self._gpu_before, (now, running)
        if before is None:
            return {}
        elapsed = (now - before[0]) * 10_000_000  # 100ns の単位
        usage: dict[int, float] = {}
        for instance, value in running.items():
            if instance not in before[1] or elapsed <= 0:
                continue
            pid = int(instance.split("_")[1])  # pid_<pid>_luid_..._eng_<n>_engtype_<種類>
            percent = max(0.0, value - before[1][instance]) / elapsed * 100
            usage[pid] = max(usage.get(pid, 0.0), percent)  # プログラムの使用率は、エンジンごとの最大（タスクマネージャーと同じ）
        return usage

    def _cpu(self, processes: Mapping[int, dict]) -> dict[int, float]:
        import psutil

        now = time.monotonic()
        seconds = {
            pid: info["cpu_times"].user + info["cpu_times"].system
            for pid, info in processes.items()
            if info.get("cpu_times") is not None and pid != 0  # pid 0 は、CPUが何もしていない時間（System Idle Process）
        }
        before, self._cpu_before = self._cpu_before, (now, seconds)
        if before is None:
            return {}
        capacity = (now - before[0]) * (psutil.cpu_count() or 1)  # PC全体に対する割合にする
        if capacity <= 0:
            return {}
        return {
            pid: max(0.0, used - before[1][pid]) / capacity * 100
            for pid, used in seconds.items()
            if pid in before[1]
        }


def _outside(usage: Mapping[int, float], nirai: set[int]) -> dict[int, float]:
    return {pid: percent for pid, percent in usage.items() if pid not in nirai}


def _with_descendants(roots: Iterable[int], parent_of: Mapping[int, int | None]) -> set[int]:
    found = set(roots)
    grew = True
    while grew:
        grew = False
        for pid, parent in parent_of.items():
            if pid not in found and parent in found:
                found.add(pid)
                grew = True
    return found


def _fullscreen() -> bool:
    state = ctypes.c_int(0)
    result = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    if result != 0:
        raise OSError(f"SHQueryUserNotificationState: 0x{result & 0xFFFFFFFF:08X}")
    return state.value in _FULLSCREEN_STATES


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def last_input_away_seconds() -> float | None:
    """Windowsの最後の入力からの秒数。取得不可なら不在とは推定せずNone。"""
    try:
        user = ctypes.windll.user32
        kernel = ctypes.windll.kernel32
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user.GetLastInputInfo(ctypes.byref(info)):
            return None
        kernel.GetTickCount.restype = wintypes.DWORD
        elapsed_ms = (kernel.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except (AttributeError, OSError, ValueError):
        return None


# --- GPU Engine カウンター（PDH）--------------------------------------------------------------
# 「動いていた時間」（Running Time。100ns 単位の積み上げ）を毎回新しい問い合わせで読み、前の値との差から使用率を出す。
# 使用率のカウンターを開いたままにするより、あとから始まったプログラムも必ず入り、前の測りからの全部の間をならせる。

_PDH_FMT_LARGE = 0x00000400
_PDH_MORE_DATA = 0x800007D2
_PDH_CSTATUS_VALID_DATA = 0x0
_PDH_CSTATUS_NEW_DATA = 0x1
_GPU_RUNNING_TIME = "\\GPU Engine(*)\\Running Time"


class _PdhValue(ctypes.Structure):
    class _Union(ctypes.Union):
        _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong),
                    ("AnsiStringValue", ctypes.c_char_p), ("WideStringValue", ctypes.c_wchar_p)]

    _fields_ = [("CStatus", wintypes.DWORD), ("value", _Union)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhValue)]


def _gpu_running_time() -> dict[str, int]:
    """GPUのエンジンごとの、動いていた時間（100ns）。インスタンス名 → 値。"""
    pdh = ctypes.windll.pdh
    query = wintypes.HANDLE()
    _pdh_ok(pdh.PdhOpenQueryW(None, None, ctypes.byref(query)), "PdhOpenQueryW")
    try:
        counter = wintypes.HANDLE()
        _pdh_ok(pdh.PdhAddEnglishCounterW(query, _GPU_RUNNING_TIME, None, ctypes.byref(counter)), "PdhAddEnglishCounterW")
        _pdh_ok(pdh.PdhCollectQueryData(query), "PdhCollectQueryData")
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(counter, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None)
        if status & 0xFFFFFFFF != _PDH_MORE_DATA:
            _pdh_ok(status, "PdhGetFormattedCounterArrayW")
            return {}  # インスタンスがない（GPUを使っているプログラムがない）
        buffer = (ctypes.c_byte * size.value)()
        _pdh_ok(pdh.PdhGetFormattedCounterArrayW(counter, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buffer),
                "PdhGetFormattedCounterArrayW")
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhItem * count.value)).contents
        return {
            item.szName: item.FmtValue.value.largeValue
            for item in items
            if item.FmtValue.CStatus in (_PDH_CSTATUS_VALID_DATA, _PDH_CSTATUS_NEW_DATA) and item.szName.startswith("pid_")
        }
    finally:
        pdh.PdhCloseQuery(query)


def _pdh_ok(status: int, what: str) -> None:
    if status != 0:
        raise OSError(f"{what}: 0x{status & 0xFFFFFFFF:08X}")
