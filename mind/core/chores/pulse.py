"""Pulse 文面生成（Brain 呼び出し口）。合意台帳 §3.6 / OSS #10。

発火判定は idle_policy.py（決定論）。ここは材料組み立てと Brain 発注のみ。
定型文をコードに埋め込まない。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mind.core.chores.idle_policy import PulseCandidate
from mind.core.config import ThresholdsConfig
from mind.core.context.emotion_render import render_emotion_for_pack
from mind.core.state.emotion import EmotionState


@dataclass(frozen=True)
class PulseGenerationContext:
    """Brain へ渡す Pulse 生成用の材料（persona 注入は pack 経由）。"""

    candidate: PulseCandidate
    persona_text: str
    absolute_rules: str
    prefs_summary: str
    relation_summary: str
    emotion: EmotionState
    thresholds: ThresholdsConfig


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
    emotion_line = render_emotion_for_pack(ctx.emotion, ctx.thresholds)
    material = ctx.candidate.context
    return (
        f"{ctx.persona_text}\n\n{ctx.absolute_rules}\n\n"
        f"{PULSE_GENERATION_INSTRUCTION}\n\n"
        f"【Pulse種別】{ctx.candidate.kind}\n"
        f"【材料】{material}\n"
        f"【いまの心】{emotion_line}\n"
        f"【好み要約】{ctx.prefs_summary or '（なし）'}\n"
        f"【関係要約】{ctx.relation_summary or '（なし）'}\n"
    )


def generate_pulse_message(
    ctx: PulseGenerationContext,
    *,
    brain_call: Callable[[str], str],
) -> str:
    """Brain（raw_call 等）で Pulse 文面を生成する。失敗時は空文字（呼び出し側が握る）。"""
    prompt = build_pulse_prompt(ctx)
    text = brain_call(prompt).strip()
    return text
