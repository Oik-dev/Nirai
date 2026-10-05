"""Pulse 文面生成（Brain 呼び出し口）。設計書 §2.8。

発火判定は idle_policy.py（決定論）。ここは材料組み立てと Brain 発注のみ。
定型文をコードに埋め込まない。材料は、話しかけるわけ（候補）・今の気持ち（文脈パックの⑤と同じ文）・今の自分。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mind.core.chores.idle_policy import PulseCandidate


@dataclass(frozen=True)
class PulseGenerationContext:
    """Brain へ渡す Pulse 生成用の材料。"""

    candidate: PulseCandidate
    persona_text: str
    absolute_rules: str
    feeling_text: str = ""  # 今の気持ち（core/feeling/feelings.py の for_pack）
    self_text: str = ""  # 今の自分（目覚めたときに本人が書いたもの。core/memory/waking.py）


PULSE_GENERATION_INSTRUCTION = """
【能動 Pulse】
マスターへ能動的に声をかける短い通知文を1つだけ書いてください。
種別・材料は下記のとおり。会話の続きではなく、GUI 通知として届く一文です。
- 定型句のコピペ禁止。毎回その場で書く
- 一人称「私」、二人称「マスター」。タメ口。絵文字禁止
- 1〜3文、短く
""".strip()


def build_pulse_prompt(ctx: PulseGenerationContext) -> str:
    """Pulse 文面生成用プロンプト。会話 pack と同型の persona 注入を先頭に置く。"""
    parts = [
        f"{ctx.persona_text}\n\n{ctx.absolute_rules}\n\n{PULSE_GENERATION_INSTRUCTION}\n",
        f"【Pulse種別】{ctx.candidate.kind}",
        f"【材料】{ctx.candidate.context}",
    ]
    if ctx.self_text:
        parts.append(f"【今の自分】\n{ctx.self_text}")
    if ctx.feeling_text:
        parts.append(f"【いまの心】\n{ctx.feeling_text}")
    return "\n".join(parts) + "\n"


def generate_pulse_message(
    ctx: PulseGenerationContext,
    *,
    brain_call: Callable[[str], str],
) -> str:
    """Brain（raw_call 等）で Pulse 文面を生成する。失敗時は空文字（呼び出し側が握る）。"""
    prompt = build_pulse_prompt(ctx)
    text = brain_call(prompt).strip()
    return text
