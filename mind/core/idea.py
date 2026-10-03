"""イデアの場所（Nirai v3 全体構想 §0・§2）。

イデアは、脳やNiraiが替わっても変わらない住人の本体。精神（このmindのコード）は住人の間で共通で、
住人ごとのもの（人格・記憶・状態・人生の記録）はすべてイデアのフォルダーに置く。
どの住人のイデアかは、起動時に環境変数 NIRAI_IDEA で渡す（住人1人につき1プロセス）。
イデアの中で何がどこにあるかは、このモジュールだけが決める（Idea）。

身元（identity.toml）のないフォルダーでは動かない。イデアを見失ったまま動くと、空の記憶で目覚めてしまうため。

このプロセスの住人のもの（IDEA・IDEA_DIR・DATA_DIR など）は、初めて使われたときに NIRAI_IDEA から決まる。
イデアを引数で受け取る道具（記憶テスト、記憶の作り直し）は、NIRAI_IDEA がなくても Idea を使える。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

IDEA_ENV = "NIRAI_IDEA"


@dataclass(frozen=True)
class Idea:
    """ひとつのイデアのフォルダーと、その中の置き場所。"""

    root: Path

    @classmethod
    def open(cls, path: Path | str) -> Idea:
        root = Path(path).resolve()
        if not (root / "identity.toml").is_file():
            raise RuntimeError(f"{root} はイデアのフォルダーではありません（identity.toml がありません）")
        return cls(root)

    @property
    def name(self) -> str:
        return tomllib.loads((self.root / "identity.toml").read_text(encoding="utf-8"))["name"]

    @property
    def persona(self) -> Path:
        return self.root / "persona"  # 人格。可変ブロックは本人が改訂する

    @property
    def data(self) -> Path:
        return self.root / "data"  # 索引と状態（記録と記憶から作り直せるもの、と今の気分など）

    @property
    def life(self) -> Path:
        return self.root / "life"  # 記憶DBから書き出した、人が読むための人生の記録

    @property
    def lifelog(self) -> Path:
        return self.root / "lifelog"  # 生ログ（経験の原文の正本。追記のみ。消すのはMasterが明示したときだけ）

    @property
    def conversation(self) -> Path:
        return self.lifelog / "conversation"

    @property
    def legacy(self) -> Path:
        return self.lifelog / "legacy"  # 継承した原本（ChatGPT時代の会話・日記・構造化記憶・継承記憶）

    @property
    def memory(self) -> Path:
        return self.root / "memory"  # 記憶。本人の言葉のページ（docs/plans/長期記憶の作り直し.md §3）

    @property
    def memory_index(self) -> Path:
        return self.data / "memory_index.db"  # 記憶の索引。memory と lifelog からいつでも作り直せる

    @property
    def memory_test(self) -> Path:
        return self.data / "memory_test"  # 記憶テストの問題集と結果


@cache
def _process_idea() -> Idea:
    raw = os.environ.get(IDEA_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"環境変数 {IDEA_ENV} に、住人のイデアのフォルダーを指定してください（例: D:\\Products\\Residents\\Serina）"
        )
    return Idea.open(raw)


_PROCESS = {
    "IDEA": lambda idea: idea,
    "IDEA_DIR": lambda idea: idea.root,
    "RESIDENT_NAME": lambda idea: idea.name,
    "PERSONA_DIR": lambda idea: idea.persona,
    "DATA_DIR": lambda idea: idea.data,
    "LIFE_DIR": lambda idea: idea.life,
    "LIFELOG_DIR": lambda idea: idea.lifelog,
}


def __getattr__(name: str):  # noqa: ANN202 — このプロセスの住人のものは、使われたときに決める（PEP 562）
    if name in _PROCESS:
        return _PROCESS[name](_process_idea())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
