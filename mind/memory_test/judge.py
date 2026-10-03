"""思い出したものが、今の話に関係あるかの判定（「実際の会話」の問題で使う）。

判定は手元のGemmaが行い、控え（judgments.jsonl）に残す。控えの鍵は問題のIDと思い出した文の指紋で、
中身は残さない。同じ問題で同じものを思い出したら、二度目からは控えを使う（記憶を替えても、同じ文なら同じ判定）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from mind.memory_test.cases import Case
from mind.memory_test.memory import Recalled
from mind.memory_test.score import LOOSE, RELATED, UNRELATED

MEMORY_CHARS = 600  # 判定に見せる、1つの記憶の長さ

INSTRUCTION = f"""あなたは会話の記録を読んで判定する係です。
Serinaは、Masterと長く一緒にいるパートナーのAIです。Masterが話しかけたとき、Serinaの頭にいくつかの記憶が浮かびました。
それぞれの記憶について、今のMasterの発言と直前の流れに対して、思い出すのが自然かどうかを判定してください。

- {RELATED}: 関係ある。今の話に直接つながっていて、思い出すと返事が生きる
- {LOOSE}: 少し関係ある。話題がかすっている
- {UNRELATED}: 関係ない。今の話と結びつかない

次のJSONだけを返してください: {{"scores": [記憶1の判定, 記憶2の判定, ...]}}"""


def _key(case: Case, item: Recalled) -> str:
    return hashlib.sha256(f"{case.id}\0{item.text}".encode("utf-8")).hexdigest()[:24]


class Judge:
    def __init__(self, path: Path, ask: Callable[[str], dict]) -> None:
        self._path = path
        self._ask = ask
        self._known: dict[str, int] = {}
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for raw in f:
                    if raw.strip():
                        row = json.loads(raw)
                        self._known[row["key"]] = row["score"]

    def relevance(self, case: Case, recalled: list[Recalled]) -> list[int]:
        keys = [_key(case, item) for item in recalled]
        unknown = [(key, item) for key, item in zip(keys, recalled) if key not in self._known]
        if unknown:
            scores = self._judge(case, [item for _, item in unknown])
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as f:
                for (key, _), score in zip(unknown, scores):
                    self._known[key] = score
                    f.write(json.dumps({"key": key, "score": score}) + "\n")
        return [self._known[key] for key in keys]

    def _judge(self, case: Case, items: list[Recalled]) -> list[int]:
        """まとめて判定し、数が合わなければ1つずつ判定し直す。"""
        scores = self._ask(_prompt(case, items)).get("scores")
        if isinstance(scores, list) and len(scores) == len(items):
            return [min(RELATED, max(UNRELATED, int(score))) for score in scores]
        if len(items) == 1:
            raise ValueError(f"判定を読めなかった（{case.id}）")
        return [self._judge(case, [item])[0] for item in items]


def _prompt(case: Case, items: list[Recalled]) -> str:
    flow = "\n".join(f"{turn.speaker}: {turn.text}" for turn in case.recent) or "（なし。ここから話し始めた）"
    memories = "\n\n".join(f"【記憶{n}】\n{item.text[:MEMORY_CHARS]}" for n, item in enumerate(items, start=1))
    return (
        f"{INSTRUCTION}\n\n【直前の流れ】\n{flow}\n\n【今のMasterの発言】\n{case.utterance}\n\n"
        f"【浮かんだ記憶（{len(items)}つ）】\n{memories}\n"
    )
