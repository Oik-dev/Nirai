"""文脈パック工場。設計書v2 §1.4(三段重ね), §1.5(配置規約), §3.3(個人情報フィルタ), §4.2(機微等級)

長期記憶段: recalled_memoriesを個人情報フィルタ（Core専権 §3.3）に通してから組み込む。
機微等級2はいかなる場合も出さない。機微等級1は化粧版があればそれを優先する。
組み合わせ禁止表（氏名×住所等）はPhase3以降で扱う（現時点は単体判定のみ）。
"""

from __future__ import annotations

from dataclasses import dataclass

from serina.core_v2.memory.store import MemoryRecord
from serina.core_v2.state.session import SessionState

SENSITIVITY_GRADE_NEVER_SHARE = 2
LOCAL_TURN_PLACEHOLDER = "（ローカルで交わした会話）"


def _render_turns(session: SessionState, destination_location: str | None) -> str:
    lines = []
    for turn in session.turns:
        if destination_location == "cloud" and turn.location == "local":
            lines.append(f"{turn.speaker}: {LOCAL_TURN_PLACEHOLDER}")
        else:
            lines.append(f"{turn.speaker}: {turn.text}")
    return "\n".join(lines)


def _filter_memories_for_pack(records: list[MemoryRecord]) -> list[str]:
    lines: list[str] = []
    for record in records:
        if record.sensitivity_grade >= SENSITIVITY_GRADE_NEVER_SHARE:
            continue
        if record.sensitivity_grade >= 1 and record.cosmetic_version:
            lines.append(record.cosmetic_version)
        else:
            lines.append(record.content)
    return lines


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
    recalled_memories: list[MemoryRecord] | None = None,
    destination_location: str | None = None,
) -> ContextPack:
    recent_turns_text = _render_turns(session, destination_location)
    memories_text = list(long_term_memories or [])
    if recalled_memories:
        memories_text += _filter_memories_for_pack(recalled_memories)
    return ContextPack(
        persona_text=persona_text,
        long_term_memories=memories_text,
        rolling_summary=session.rolling_summary,
        recent_turns_text=recent_turns_text,
        absolute_rules=absolute_rules,
        master_utterance=master_utterance,
    )
