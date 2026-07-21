"""文脈パック工場。設計書 §1.4(三段重ね), §1.5(配置規約・7段)。

段⑤「今のセリナの心の状態」: 感情状態(EmotionState)は生数値をBrainへ渡さず、Core側で
決定論的に意訳した自然文のみをパックへ載せる（core/context/emotion_render.py）。
ContextPackはfrozenスナップショットのため、EmotionStateオブジェクト自体は保持しない。

cloud 宛の記憶間引き・化粧版・ローカルターン伏せ字は退役（2026-07-19）。
文脈パックは常にローカル Brain 向けに記憶原文を載せる。
外相談の機微門番は `routing_rules`（相談クエリ）のみ（§3.3.1・§5.6）。

直近会話はBrainのcontext_sizeに応じた窓（§1.4）。窓から溢れた分はrolling_summaryが担う。

想起された長期記憶は created_at から相対日ラベルを付けて注入する
（例: `[2025-03-21・昨日] 本文`）。“いま起きたこと”との誤読を防ぐ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from serina.core.config import ThresholdsConfig, load_thresholds
from serina.core.context.emotion_render import render_emotion_for_pack
from serina.core.context.memory_time import format_recalled_memory
from serina.core.memory.store import MemoryRecord
from serina.core.state.emotion import EmotionState
from serina.core.state.session import SessionState

EMOTION_UNAVAILABLE_TEXT = "（感情状態は今回未接続）"


def _render_turns(
    session: SessionState,
    *,
    recent_turns_limit: int | None = None,
) -> str:
    turns = session.turns
    if recent_turns_limit is not None and recent_turns_limit >= 0:
        turns = turns[-recent_turns_limit:]
    return "\n".join(f"{turn.speaker}: {turn.text}" for turn in turns)


# B4: 静的先頭固定（合意台帳 §4-5）。人格・要約・絶対ルールを毎ターン先頭に置く。
STATIC_HEAD_MARKER = "【人格・基本ルール】"


def render_static_head(
    *,
    persona_text: str,
    absolute_rules: str,
    prefs_summary: str = "",
    relation_summary: str = "",
) -> str:
    """パック静的先頭（キャッシュ席）。順序: 人格 → 好み要約 → 関係要約 → 絶対ルール。"""
    from serina.core.chores.summaries import (
        PREFS_SUMMARY_MARKER,
        RELATION_SUMMARY_MARKER,
    )

    parts = [STATIC_HEAD_MARKER, persona_text]
    if prefs_summary:
        parts.append(f"{PREFS_SUMMARY_MARKER}\n{prefs_summary}")
    if relation_summary:
        parts.append(f"{RELATION_SUMMARY_MARKER}\n{relation_summary}")
    parts.append(absolute_rules)
    return "\n".join(parts) + "\n\n"


@dataclass(frozen=True)
class ContextPack:
    """§1.5の7段構成を保持する。render()で配置規約どおりの順に並べる。

    ①人格・基本ルール ②想起された長期記憶 ③今セッションの要約 ④直近の会話
    ⑤今のセリナの心の状態(新設) ⑥絶対ルールの再掲 ⑦今回のマスターの発言

    ①は静的先頭（B4）。可変部（想起・要約・会話・感情）をその後ろに置く。
    """

    persona_text: str
    prefs_summary: str
    relation_summary: str
    long_term_memories: list[str]
    rolling_summary: str
    recent_turns_text: str
    emotion_state_text: str
    absolute_rules: str
    master_utterance: str
    bundled_facts: tuple[str, ...] = ()

    def render(self) -> str:
        long_term_block = "\n".join(self.long_term_memories) if self.long_term_memories else "（Phase1: 記憶未接続）"
        facts_block = "\n".join(self.bundled_facts) if self.bundled_facts else "（該当なし）"
        summary_block = self.rolling_summary or "（まだ要約なし）"
        recent_block = self.recent_turns_text or "（直近の会話なし）"
        emotion_block = self.emotion_state_text or EMOTION_UNAVAILABLE_TEXT
        return (
            render_static_head(
                persona_text=self.persona_text,
                absolute_rules=self.absolute_rules,
                prefs_summary=self.prefs_summary,
                relation_summary=self.relation_summary,
            )
            + f"【想起された長期記憶】\n{long_term_block}\n\n"
            + f"【時間付き事実（Planner併載）】\n{facts_block}\n\n"
            f"【今セッションの要約】\n{summary_block}\n\n"
            f"【直近の会話】\n{recent_block}\n\n"
            f"【今のセリナの心の状態】\n{emotion_block}\n\n"
            f"【絶対ルール（再掲）】\n{self.absolute_rules}\n\n"
            f"【今回のマスターの発言】\n{self.master_utterance}\n"
        )


def build_context_pack(
    *,
    persona_text: str,
    absolute_rules: str,
    session: SessionState,
    prefs_summary: str = "",
    relation_summary: str = "",
    master_utterance: str,
    long_term_memories: list[str] | None = None,
    recalled_memories: list[MemoryRecord] | None = None,
    bundled_facts: list[str] | None = None,
    recent_turns_limit: int | None = None,
    emotion: EmotionState | None = None,
    thresholds: ThresholdsConfig | None = None,
    now: datetime | None = None,
    # 互換: 旧 cloud 宛引数。無視する（退役）
    destination_location: str | None = None,
    routing_rules: object | None = None,
) -> ContextPack:
    del destination_location, routing_rules  # 退役パラメータ（呼び出し互換のため受け取るのみ）
    recent_turns_text = _render_turns(session, recent_turns_limit=recent_turns_limit)
    memories_text = list(long_term_memories or [])
    if recalled_memories:
        memories_text += [
            format_recalled_memory(record, now=now) for record in recalled_memories
        ]
    rolling_summary = session.rolling_summary or ""
    if emotion is None:
        emotion_state_text = EMOTION_UNAVAILABLE_TEXT
    else:
        emotion_state_text = render_emotion_for_pack(emotion, thresholds or load_thresholds())
    return ContextPack(
        persona_text=persona_text,
        prefs_summary=prefs_summary,
        relation_summary=relation_summary,
        long_term_memories=memories_text,
        rolling_summary=rolling_summary,
        recent_turns_text=recent_turns_text,
        emotion_state_text=emotion_state_text,
        absolute_rules=absolute_rules,
        master_utterance=master_utterance,
        bundled_facts=tuple(bundled_facts or ()),
    )
