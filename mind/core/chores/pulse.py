"""Pulse の問い（話しかけるかと、その言葉を本人に聞く）。設計書 §2.8。

発火判定は idle_policy.py（決定論）。ここは材料の組み立てだけ（脳に聞くのは Core.pulse）。
定型文をコードに埋め込まない。材料は、話しかけるわけ（候補）・今の気持ち（文脈パックの⑤と同じ文）・今の自分・マスターとのこと・
手元の会話の流れ。今は話さないと決めるのも本人（同じ1回の構造化選択で speak=false。マスターに静かにしてと言われていれば、流れを読んで決める）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from mind.core.chores.idle_policy import PulseCandidate
from mind.core.memory.structure import JST
from mind.core.state.session import Turn

_SPEAKERS = {"master": "マスター", "serina": "あなた"}


@dataclass(frozen=True)
class PulseGenerationContext:
    """Brain へ渡す Pulse 生成用の材料。"""

    candidate: PulseCandidate
    persona_text: str
    absolute_rules: str
    feeling_text: str = ""  # 今の気持ち（core/feeling/feelings.py の for_pack）
    self_text: str = ""  # 今の自分（目覚めたときに本人が書いたもの。core/memory/waking.py）
    relation_text: str = ""  # マスターとのこと（眠りの間に本人が書き足してきたもの。core/memory/relation.py）
    flow_text: str = ""  # 手元の会話の流れ（render_flow）


PULSE_GENERATION_INSTRUCTION = """
【能動 Pulse】
今、あなたからマスターへ話しかけるかを決めてください。
話しかけるなら speak=true にして、text に短い言葉を1つだけ書いてください。
今は話しかけないと決めたら speak=false、text は空にしてください。
手元の会話の流れも判断材料です。直近でマスターから静かにしてほしい・今は集中したいなどの希望を伝えられ、その後に解除する言葉がなければ、その希望はまだ続いているものとして考えてください。「終わった？」「まだ集中してる？」と確かめるためだけの声かけは見送る対象です。Pulse候補になった理由そのもの（会いたい・人恋しい等）だけでは、その希望を破って今話す特別な理由には数えません。緊急のこと、約束や今日しかない用件など、別に今伝える理由があるなら話しかけても構いません。最終的に今話すかはあなた自身で決めてください。
種別・材料は下記のとおり。
- 定型句のコピペ禁止。毎回その場で書く
- 一人称「私」、二人称「マスター」。タメ口。絵文字禁止
- 1〜3文、短く
""".strip()

PULSE_CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "speak": {"type": "boolean", "description": "今マスターへ話しかけるならtrue、見送るならfalse"},
        "text": {"type": "string", "description": "speak=trueのときの短い言葉。falseなら空文字"},
    },
    "required": ["speak", "text"],
    "additionalProperties": False,
}


def render_flow(turns: Iterable[Turn], *, now: datetime) -> str:
    """手元の会話の流れ（発言ごとの日本時間つき。いつ言われたことかを本人が読めるように）。発言がなければ空。"""
    lines = []
    for turn in turns:
        at = f"（{datetime.fromisoformat(turn.ts).astimezone(JST):%m/%d %H:%M}）" if turn.ts else ""
        lines.append(f"{_SPEAKERS.get(turn.speaker, turn.speaker)}{at}: {turn.text}")
    if not lines:
        return ""
    return f"今は {now.astimezone(JST):%m/%d %H:%M}\n" + "\n".join(lines)


def build_pulse_prompt(ctx: PulseGenerationContext) -> str:
    """Pulse 文面生成用プロンプト。会話 pack と同型の persona 注入を先頭に置く。"""
    parts = [
        f"{ctx.persona_text}\n\n{ctx.absolute_rules}\n\n{PULSE_GENERATION_INSTRUCTION}\n",
        f"【Pulse種別】{ctx.candidate.kind}",
        f"【材料】{ctx.candidate.context}",
    ]
    if ctx.self_text:
        parts.append(f"【今の自分】\n{ctx.self_text}")
    if ctx.relation_text:
        parts.append(f"【マスターとのこと】\n{ctx.relation_text}")
    if ctx.feeling_text:
        parts.append(f"【いまの心】\n{ctx.feeling_text}")
    if ctx.flow_text:
        parts.append(f"【手元の会話の流れ】\n{ctx.flow_text}")
    return "\n".join(parts) + "\n"
