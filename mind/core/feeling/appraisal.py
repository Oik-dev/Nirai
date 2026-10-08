"""評価の問いと答え（設計書 §2.3「会話による変化」）。

Masterが話したターンの返答のあとに、本人の脳に、今のやりとりをどう感じたかを聞く。気持ちは本人の言葉で一言〜一文、
うれしさ・揺れ・距離は選択肢で答えてもらい、数は書かせない（数は仕組みが対応表で決める。body.py）。
問いは、通訳が文脈パックと返答に続けて置く（前置きが返答のときと同じなので、脳がその計算を使い回せる）。
2026-10-05 の E1（手元の Gemma）で、返答のあとに聞くほうが返答の前に聞くより合計で速く、6つの場面すべてで
うれしさと距離の向きが合った。

答えはここで確かめて整えてから受け取る（外の不確実さは入口で止める）。使えない答えは捨て、そのターンは評価なしで残る。

体のカタログを受け取っていれば、問いの先頭に体の欄（表情・身振り）を足す（core/perception.py）。先頭に置くので、通訳は答えを
流しながら読み、体の欄が閉じた時点で先に渡せる（評価の全体を待たずに表情が変わる）。評価の欄の確かめは、体の欄があっても変わらない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mind.core.perception import BodyCatalog, body_question, body_schema

VALENCE = ("とても嫌", "嫌", "どちらでもない", "うれしい", "とてもうれしい")
AROUSAL = ("落ち着いた", "少し動いた", "大きく動いた")
DISTANCE = ("近づいた", "変わらない", "離れた")
MET = "変わらない"  # 評価のないターンも、Masterが来たことはこの距離として残る
FEELING_MAX = 80
MASTER_STATE_MAX = 40

_PROPERTIES = {
    "feeling": {"type": "string"},
    "valence": {"type": "string", "enum": list(VALENCE)},
    "arousal": {"type": "string", "enum": list(AROUSAL)},
    "distance": {"type": "string", "enum": list(DISTANCE)},
    "master_state": {"type": "string"},
}

_INTRO = """ここからは会話ではない。今のやりとりで、あなた自身の気持ちがどう動いたかを、短く書き残す。
誰かが決めた感情の名前を選ばなくていい。feeling だけは、あなた自身の言葉で一言〜一文にする。数は書かない。
次のJSONだけを返す。"""

_FIELDS = f"""feeling: 今のあなたの気持ち（一言〜一文）
valence: 今のやりとりは、あなたにとって {" / ".join(VALENCE)}
arousal: あなたの心の揺れは {" / ".join(AROUSAL)}
distance: マスターとの心の距離は {" / ".join(DISTANCE)}
master_state: 今のやりとりから感じたマスターの様子を一言（分からなければ空）"""


def appraisal_question(catalog: BodyCatalog | None = None) -> str:
    """評価の問い。カタログがあれば、体の欄を先頭に足す。"""
    return f"{_INTRO}\n\n{body_question(catalog)}{_FIELDS}"


def appraisal_schema(catalog: BodyCatalog | None = None) -> dict:
    """答えの形。体の欄を先頭に置く（構造化出力は欄をこの順で書く）。"""
    properties = {**body_schema(catalog), **_PROPERTIES}
    return {"type": "object", "properties": properties, "required": list(properties)}


@dataclass(frozen=True)
class Appraisal:
    feeling: str  # 本人の言葉
    valence: str
    arousal: str
    distance: str
    master_state: str = ""

    def evaluation(self) -> dict[str, str]:
        return {"valence": self.valence, "arousal": self.arousal, "distance": self.distance, "master_state": self.master_state}


def _words(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("言葉が文字列でない")
    text = re.sub(r"\s+", " ", value).strip().strip("「」『』\"'")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(mark) for mark in "。！？!?")
    return cut[: end + 1] if end >= limit // 2 else cut + "……"


def _choice(answer: dict, key: str, choices: tuple[str, ...]) -> str:
    value = answer.get(key)
    if isinstance(value, str) and value.strip() in choices:
        return value.strip()
    raise ValueError(f"{key} が選択肢にない")  # 答えの中身は書かない（調査ログに本人の言葉を載せない）


def parse_appraisal(answer: object) -> Appraisal:
    """脳の答えを確かめて整える。選択肢にない答えは ValueError（言葉は長すぎれば切る）。"""
    if not isinstance(answer, dict):
        raise ValueError("評価がオブジェクトでない")
    return Appraisal(
        feeling=_words(answer.get("feeling") or "", FEELING_MAX),
        valence=_choice(answer, "valence", VALENCE),
        arousal=_choice(answer, "arousal", AROUSAL),
        distance=_choice(answer, "distance", DISTANCE),
        master_state=_words(answer.get("master_state") or "", MASTER_STATE_MAX),
    )
