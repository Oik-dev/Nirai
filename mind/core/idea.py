"""イデアの場所（Nirai v3 全体構想 §0・§2）。

イデアは、脳やNiraiが替わっても変わらない住人の本体。精神（このmindのコード）は住人の間で共通で、
住人ごとのもの（人格・記憶DB・状態・人生の記録）はすべてイデアのフォルダーに置く。
どの住人のイデアかは、起動時に環境変数 NIRAI_IDEA で渡す（住人1人につき1プロセス）。
イデアの中で何がどこにあるかは、このモジュールだけが決める。

身元（identity.toml）のないフォルダーでは動かない。イデアを見失ったまま動くと、空の記憶で目覚めてしまうため。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

IDEA_ENV = "NIRAI_IDEA"


def _resolve_idea_dir() -> Path:
    raw = os.environ.get(IDEA_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"環境変数 {IDEA_ENV} に、住人のイデアのフォルダーを指定してください（例: D:\\Products\\Residents\\Serina）"
        )
    idea_dir = Path(raw).resolve()
    if not (idea_dir / "identity.toml").is_file():
        raise RuntimeError(f"{idea_dir} はイデアのフォルダーではありません（identity.toml がありません）")
    return idea_dir


IDEA_DIR = _resolve_idea_dir()
RESIDENT_NAME = tomllib.loads((IDEA_DIR / "identity.toml").read_text(encoding="utf-8"))["name"]
PERSONA_DIR = IDEA_DIR / "persona"  # 人格。可変ブロックは本人が改訂する
DATA_DIR = IDEA_DIR / "data"  # 記憶DBと状態
LIFE_DIR = IDEA_DIR / "life"  # 記憶DBから書き出した、人が読むための人生の記録
LIFELOG_DIR = IDEA_DIR / "lifelog"  # 生ログ（経験の原文の正本。追記のみ。消すのはMasterが明示したときだけ）
