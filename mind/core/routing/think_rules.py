"""think ON/OFF のルール先行判定（§3.1改訂）。2026-07-20 応答高速化。

規則で確信できる発話は LLM を呼ばずに即決し、迷う中間帯だけ Brain.judge へ相談する。
狙いは体感速度: 雑談の大半をルールの False で即決し、返答生成前の判定発注
（LLM 1往復＝数秒〜十数秒）を省く。判定の既定は設計書 §3.1「曖昧は false（速度優先）」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 即 think:true。明示の深考要求・明らかに多段推論が要る話題のみ（誤爆すると毎ターン遅くなる
# ため保守的に。「考えてる」等の日常表現を巻き込まないよう「〜考えて」は副詞付きに限定）。
DEEP_MARKERS: tuple[str, ...] = (
    "よく考えて",
    "じっくり考えて",
    "ちゃんと考えて",
    "深く考えて",
    "真剣に考えて",
    "頭を使って",
    "証明して",
    "論理パズル",
    "推論して",
)

# 規則では白黒つけられない中間帯 → judge（LLM）へ相談する。
JUDGE_MARKERS: tuple[str, ...] = (
    "どう思う",
    "どうすれば",
    "どうしたら",
    "なぜ",
    "なんで",
    "原因",
    "比較して",
    "メリット",
    "デメリット",
    "アイデア",
    "案を",
    "整理して",
    "戦略",
    "設計",
    "計画",
    "アルゴリズム",  # 雑談での言及もあるため即trueにせず中間帯（レビュー指摘 2026-07-20）
)

# 算数・数式らしき並び（例: 12+34、3 × 4）は即 think:true。
# 「-」は日付・範囲表記（2026-07-20 等）を誤って深考に倒すため演算子集合に含めない
# （引き算の聞き方は文脈語で中間帯〜既定falseに落ちる。速度優先の既定と整合）。
MATH_EXPRESSION_PATTERN = re.compile(r"\d+\s*[+*/×÷^]\s*\d+")

# これ以上の長文は要件が複雑な可能性があるため judge に回す。
LONG_UTTERANCE_THRESHOLD = 200


@dataclass(frozen=True)
class ThinkDecision:
    think: bool | None  # None = 規則では確信できない（judge へ相談）
    why: str


def plan_think(utterance: str) -> ThinkDecision:
    """発話から think 要否を規則判定する。確信が持てなければ think=None を返す。"""
    for marker in DEEP_MARKERS:
        if marker in utterance:
            return ThinkDecision(think=True, why=f"規則: 深考マーカー「{marker}」")
    if MATH_EXPRESSION_PATTERN.search(utterance):
        return ThinkDecision(think=True, why="規則: 数式らしき並びを検出")
    for marker in JUDGE_MARKERS:
        if marker in utterance:
            return ThinkDecision(think=None, why=f"規則: 中間帯マーカー「{marker}」")
    if len(utterance) >= LONG_UTTERANCE_THRESHOLD:
        return ThinkDecision(think=None, why="規則: 長文のため判定を委任")
    return ThinkDecision(think=False, why="規則: 深考の手がかりなし（速度優先）")
