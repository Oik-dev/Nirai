"""GPU見送り判定（core/chores/gpu_guard.py）のテスト。設計書 §2.4。

nvidia-smi が無い・失敗する環境では、見送らずに続ける（fail-open）。GPU番人の不調で裏方が永久に止まらないため。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.gpu_guard import is_gpu_busy


def test_is_gpu_busy_fails_open_without_nvidia_smi() -> None:
    """nvidia-smiが無い/失敗する環境ではFalse(=見送らず継続)を返す。閾値が実環境ではありえない高さでも同じ。"""
    assert is_gpu_busy(threshold_percent=1_000_000.0) is False
