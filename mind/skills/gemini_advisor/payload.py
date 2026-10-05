"""アドバイザー向けクラウド送信ペイロード（相談クエリのみ）。設計書 §5.6 / routing_rules 門番。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SensitivityRules(Protocol):
    """機微判定の受け口。実体は Core が引数で渡す（Skill から Core を参照し返さない。設計書 §1.3）。"""

    def is_sensitive(self, text: str) -> bool: ...

FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "persona",
    "persona_text",
    "boundary",
    "absolute_rules",
    "memory",
    "memories",
    "recalled_memories",
    "session",
    "feeling",
    "appraisal",
    "self_assessment",
})


@dataclass(frozen=True)
class AdvisorPayload:
    """Gemini API に載せてよい最小ペイロード。"""

    query: str
    category: str

    def to_api_body(self, *, system_instruction: str) -> dict:
        return {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": self.query}]}],
        }

    def to_audit_dict(self) -> dict:
        """テスト用: 送信 JSON に人格・記憶が含まれないことを検証する。"""
        return {"query": self.query, "category": self.category}


def sanitize_query(
    query: str,
    *,
    routing_rules: SensitivityRules | None = None,
) -> str | None:
    """相談クエリを検査する。機微（等級2相当）と判定されたら None（送信しない）。

    fail-closed（2026-07-31 是正・Phase C2）: `routing_rules` が None（未接続）の場合も
    None を返す。設計書 §5.6 の既定拒否規範（門番未接続の直呼びで相談クエリが素通しに
    なる経路を許さない）と実装を一致させる。旧実装は `routing_rules is None` のとき
    判定自体をスキップして通す fail-open だった（原典との乖離。architecture-reviewer
    5回目指摘）。`GeminiAdvisorSkill.consult()` 側の早期リターンは二重の安全網として
    そのまま残る。本関数を直に呼ぶ将来の別経路に対しても安全側にするための是正。
    """
    cleaned = query.strip()
    if not cleaned:
        return None
    if routing_rules is None or routing_rules.is_sensitive(cleaned):
        return None
    return cleaned


def build_payload(
    query: str,
    *,
    category: str = "general",
    routing_rules: SensitivityRules | None = None,
) -> AdvisorPayload | None:
    safe_query = sanitize_query(query, routing_rules=routing_rules)
    if safe_query is None:
        return None
    cat = (category or "general").strip().lower()
    return AdvisorPayload(query=safe_query, category=cat)
