"""魂の場所（Nirai v3 全体構想 §2）。

心のコードは住人の間で共通。住人ごとのもの（人格・記憶DB・状態・人生の記録）は、魂のフォルダーに置く。
どの住人の魂かは、起動時に環境変数 NIRAI_SOUL で渡す（住人1人につき1プロセス）。
魂の中で何がどこにあるかは、このモジュールだけが決める。

身元（identity.toml）のないフォルダーでは動かない。魂を見失ったまま動くと、空の記憶で目覚めてしまうため。
"""

from __future__ import annotations

import os
from pathlib import Path

SOUL_ENV = "NIRAI_SOUL"


def _resolve_soul_dir() -> Path:
    raw = os.environ.get(SOUL_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"環境変数 {SOUL_ENV} に、住人の魂のフォルダーを指定してください（例: D:\\Products\\Residents\\Serina）"
        )
    soul_dir = Path(raw).resolve()
    if not (soul_dir / "identity.toml").is_file():
        raise RuntimeError(f"{soul_dir} は魂のフォルダーではありません（identity.toml がありません）")
    return soul_dir


SOUL_DIR = _resolve_soul_dir()
PERSONA_DIR = SOUL_DIR / "persona"  # 人格。可変ブロックは本人が改訂する
DATA_DIR = SOUL_DIR / "data"  # 記憶DBと状態
LIFE_DIR = SOUL_DIR / "life"  # 記憶DBから書き出した、人が読むための人生の記録
LIFELOG_DIR = SOUL_DIR / "lifelog"  # 生ログ（追記のみ。消さない）
