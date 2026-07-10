"""文脈パック工場。設計書v2 §1.4(三段重ね), §1.5(配置規約)

Phase1範囲: 短期(直近会話)・中期(セッション要約)のみを組み立てる。
長期(記憶DB想起)はPhase2で core/memory と接続する。それまでは空リストを保持する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from serina.core_v2.state.session import SessionState


@dataclass(frozen=True)
class ContextPack:
    """§1.5の6段構成を保持する。render()で配置規約どおりの順に並べる。"""

    persona_text: str
    long_term_memories: list[str]
    rolling_summary: str
    recent_turns_text: str
    absolute_rules: str
    master_utterance: str

    def render(self) -> str:
        long_term_block = "\n".join(self.long_term_memories) if self.long_term_memories else "（Phase1: 記憶未接続）"
        return (
            f"【人格・基本ルール】\n{self.persona_text}\n{self.absolute_rules}\n\n"
            f"【想起された長期記憶】\n{long_term_block}\n\n"
            f"【今セッションの要約】\n{self.rolling_summary}\n\n"
            f"【直近の会話】\n{self.recent_turns_text}\n\n"
            f"【絶対ルール（再掲）】\n{self.absolute_rules}\n\n"
            f"【今回のマスターの発言】\n{self.master_utterance}\n"
        )


def build_context_pack(
    *,
    persona_text: str,
    absolute_rules: str,
    session: SessionState,
    master_utterance: str,
    long_term_memories: list[str] | None = None,
) -> ContextPack:
    recent_turns_text = "\n".join(f"{t.speaker}: {t.text}" for t in session.turns)
    return ContextPack(
        persona_text=persona_text,
        long_term_memories=long_term_memories or [],
        rolling_summary=session.rolling_summary,
        recent_turns_text=recent_turns_text,
        absolute_rules=absolute_rules,
        master_utterance=master_utterance,
    )
