"""GPU見送り判定。設計書 §2.4「会話と裏方の同時進行ルール」。

裏方便でAuroraを使う前にGPU使用率を確認し、高負荷（設定値。ゲーム中等）なら
発注を見送って宿題箱に留める。マスターの遊びを妨げない。

nvidia-smiが無い・失敗する環境では安全側＝「忙しくない」として処理を継続する
（GPU番人自体の不調で裏方便が永久停止するのは本末転倒なため、失敗時はfail-open）。
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

_NVIDIA_SMI_ARGS = (
    "nvidia-smi",
    "--query-gpu=utilization.gpu",
    "--format=csv,noheader,nounits",
)
_TIMEOUT_SECONDS = 3
_warned_once = False


def is_gpu_busy(threshold_percent: float) -> bool:
    """GPU使用率がthreshold_percentを超えていればTrue。取得できない場合はFalse(fail-open)。"""
    global _warned_once
    try:
        proc = subprocess.run(
            _NVIDIA_SMI_ARGS,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=True,
        )
        first_line = proc.stdout.strip().splitlines()[0]
        usage = float(first_line.strip())
    except Exception:  # noqa: BLE001 — nvidia-smi不在・タイムアウト・解析失敗すべて同じ扱い
        if not _warned_once:
            logger.info("GPU使用率を取得できないため、GPU見送り判定なしで裏方便を継続します")
            _warned_once = True
        return False
    return usage > threshold_percent
