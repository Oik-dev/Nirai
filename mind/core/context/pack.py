"""文脈パック工場。設計書 §1.4(三段重ね), §1.5(配置規約・8段)。

段⑤「今のセリナの心の状態」: 感情状態(EmotionState)は生数値をBrainへ渡さず、Core側で
決定論的に意訳した自然文のみをパックへ載せる（core/context/emotion_render.py）。
ContextPackはfrozenスナップショットのため、EmotionStateオブジェクト自体は保持しない。

cloud 宛の記憶間引き・化粧版・ローカルターン伏せ字は退役（2026-07-19）。
文脈パックは常にローカル Brain 向けに記憶原文を載せる。
外相談の機微門番は `routing_rules`（相談クエリ）のみ（§3.3.1・§5.6）。

④粗い要約 / ⑦細かめ要約は session.rolling_summary / session.fine_summary を載せる。
直近ターン原文窓をパックへ載せる方式は退役（§1.5）。

想起された長期記憶は created_at から相対日ラベルを付けて注入する
（例: `[2025-03-21・昨日] 本文`）。“いま起きたこと”との誤読を防ぐ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mind.core.config import ThresholdsConfig, load_thresholds
from mind.core.context.emotion_render import render_emotion_for_pack
from mind.core.context.memory_time import format_recalled_memory
from mind.core.context.relationship_render import render_master_observation_for_pack
from mind.core.memory.store import MemoryRecord
from mind.core.state.desire import DesireState
from mind.core.state.emotion import EmotionState
from mind.core.state.relationship import RelationshipState
from mind.core.state.session import SessionState

EMOTION_UNAVAILABLE_TEXT = "（感情状態は今回未接続）"

# rolling_summary.py の _speaker_label と同じ変換（層をまたぐ依存を避けるため複製）。
# fine_summary未到着時のフォールバック窓でも話者ラベルは日本語で揃える。
_SPEAKER_LABELS_JA = {"master": "マスター", "serina": "セリナ"}


def _render_turns(
    session: SessionState,
    *,
    recent_turns_limit: int | None = None,
) -> str:
    """⑦のフォールバックにのみ使う（`fine_summary`未到着時に直近原文を暫定で載せる）。

    2026-07-26 A9是正: 旧docstring「pack.renderでは使わない」は誤り。
    `build_context_pack`が`fine_summary = session.fine_summary or recent_turns_text`
    で実使用している（本ファイル内`fine_summary`の代入部参照）。
    """
    turns = session.turns
    if recent_turns_limit is not None and recent_turns_limit >= 0:
        turns = turns[-recent_turns_limit:]
    return "\n".join(
        f"{_SPEAKER_LABELS_JA.get(turn.speaker, turn.speaker)}: {turn.text}" for turn in turns
    )


# B4: 静的先頭固定（合意台帳 §4-5）。persona 01〜05 のみ毎ターン先頭に置く。
STATIC_HEAD_MARKER = "【人格・基本ルール】"


def render_static_head(*, persona_text: str) -> str:
    """パック静的先頭（キャッシュ席）。persona のみ（§1.5 ①）。

    2026-07-26 A9: 未使用だった互換引数（absolute_rules/prefs_summary/relation_summary）を
    削除した。呼び出し元（`ContextPack.render`・テスト）はどちらも`persona_text`しか
    渡していなかった（grep確認済み）。
    """
    return f"{STATIC_HEAD_MARKER}\n{persona_text}\n\n"


@dataclass(frozen=True)
class ContextPack:
    """§1.5の8段構成＋⑦'（2026-07-31改訂）を保持する。render()で配置規約どおりの順に並べる。

    ①人格・基本ルール ②想起された長期記憶 ③時間付き事実 ④今セッションの要約
    ⑤今のセリナの心の状態 ⑥絶対ルール ⑦直近の会話（細かめ要約）
    ⑦'外部情報（今回のみ・Gemini/Tavily窓口の材料。毎ターン非空） ⑧今回のマスターの発言

    ①は静的先頭（B4）。可変部（想起・要約・感情）をその後ろに置く。
    ⑦'は⑦⑧隣接原則の意図的な例外（§1.5参照。欠落ではない）。
    """

    persona_text: str
    prefs_summary: str
    relation_summary: str
    long_term_memories: list[str]
    rolling_summary: str
    fine_summary: str
    recent_turns_text: str
    emotion_state_text: str
    absolute_rules: str
    master_utterance: str
    bundled_facts: tuple[str, ...] = ()
    # 2026-07-26 B1: マスターの様子（直近観測）。⑤ブロック末尾へ1行添える。
    # 空文字なら省略する（鮮度切れ・未観測。core/context/relationship_render.py）。
    master_observation_text: str = ""
    # 2026-07-31: Gemini/Tavily窓口の無言統合パイプライン（Phase D）。Core が組み立てる
    # 今回限りの指示欄（persona資産ではない。prompt/persona/には置かない）。
    # 常に非空（材料が無いときも「聞いた・調べた体で話さない」という拘束条件5の
    # 最小ガード文が入る）。⑧の直前に置く（今回の発言に対する今回限りの材料のため）。
    advisor_context_text: str = ""

    def render(self) -> str:
        long_term_block = (
            "\n".join(self.long_term_memories)
            if self.long_term_memories
            else "（Phase1: 記憶未接続）"
        )
        summary_block = self.rolling_summary or "（まだ要約なし）"
        fine_block = self.fine_summary or "（まだ要約なし）"
        emotion_block = self.emotion_state_text or EMOTION_UNAVAILABLE_TEXT
        if self.master_observation_text:
            emotion_block = f"{emotion_block}\nマスターの様子: {self.master_observation_text}"

        parts = [
            render_static_head(persona_text=self.persona_text),
            f"【想起された長期記憶】\n{long_term_block}\n",
        ]
        if self.bundled_facts:
            facts_block = "\n".join(self.bundled_facts)
            parts.append(f"【時間付き事実】\n{facts_block}\n")
        parts.extend([
            f"【今セッションの要約】\n{summary_block}\n",
            f"【今のセリナの心の状態】\n{emotion_block}\n",
            f"【絶対ルール】\n{self.absolute_rules}\n",
            f"【直近の会話】\n{fine_block}\n",
        ])
        if self.advisor_context_text:
            parts.append(f"【外部情報（今回のみ）】\n{self.advisor_context_text}\n")
        parts.append(f"【今回のマスターの発言】\n{self.master_utterance}\n")
        return "\n".join(parts)


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
    schedule_fact_line: str | None = None,
    recent_turns_limit: int | None = None,
    emotion: EmotionState | None = None,
    desire: DesireState | None = None,
    relationship: RelationshipState | None = None,
    thresholds: ThresholdsConfig | None = None,
    now: datetime | None = None,
    advisor_context_text: str = "",
) -> ContextPack:
    # 2026-07-26 A9: 旧cloud宛引数（destination_location/routing_rules。cloud宛間引きは
    # 2026-07-19退役済み）を削除した。prefs_summary/relation_summaryは受け取るが
    # ContextPackへは載せない（現状未使用。§4.11の好み要約・関係要約とは別物）。
    del prefs_summary, relation_summary
    recent_turns_text = _render_turns(session, recent_turns_limit=recent_turns_limit)
    memories_text = list(long_term_memories or [])
    if recalled_memories:
        memories_text += [
            format_recalled_memory(record, now=now) for record in recalled_memories
        ]
    rolling_summary = session.rolling_summary or ""
    # 要約未到着時は直近原文を暫定で⑦に載せる（接続切れ防止。LLM更新後は fine_summary 優先）
    fine_summary = (session.fine_summary or "").strip() or recent_turns_text
    resolved_thresholds = thresholds or load_thresholds()
    if emotion is None:
        emotion_state_text = EMOTION_UNAVAILABLE_TEXT
    else:
        emotion_state_text = render_emotion_for_pack(
            emotion, resolved_thresholds, desire=desire,
        )
    # 2026-07-26 B1: マスターの様子（直近観測）。生きたRelationshipStateはパックへ
    # 持たせず、ここで意訳した文字列だけをContextPackへ渡す（条文A）。
    master_observation_text = render_master_observation_for_pack(
        relationship, resolved_thresholds, now=now,
    )
    # Task 1-6: 開いている予定窓を【時間付き事実】へ最大1件差し込む（既存fact差し込みは維持）
    facts = list(bundled_facts or [])
    if schedule_fact_line:
        facts.append(schedule_fact_line)
    return ContextPack(
        persona_text=persona_text,
        prefs_summary="",
        relation_summary="",
        long_term_memories=memories_text,
        rolling_summary=rolling_summary,
        fine_summary=fine_summary,
        recent_turns_text=recent_turns_text,
        emotion_state_text=emotion_state_text,
        absolute_rules=absolute_rules,
        master_utterance=master_utterance,
        bundled_facts=tuple(facts),
        master_observation_text=master_observation_text,
        advisor_context_text=advisor_context_text,
    )
