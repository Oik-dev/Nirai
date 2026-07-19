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
    "emotion",
    "relationship",
    "fusen_list",
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
    """相談クエリを検査する。機微（等級2相当）と判定されたら None（送信しない）。"""
    cleaned = query.strip()
    if not cleaned:
        return None
    if routing_rules is not None and routing_rules.is_sensitive(cleaned):
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
