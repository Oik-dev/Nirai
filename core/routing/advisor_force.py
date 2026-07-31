"""Gemini（お友達枠）の決定論トリガー（§3.7 / §5.6）。

Voice（persona 注入の返答生成）に呼びかけ判定を委ねない。合言葉「Gemini」の明示のみで
発火する決定論 backstop。旧版にあった鮮度・事実ドメイン・明示検索マーカーによる
自動発火（合言葉ゼロでの判定）は全廃した（2026-07-31 改訂）。
自律検索（合言葉ゼロ・毎発話判定）は Tavily レーン（`core/routing/tavily_rules.py`）が担う。
旧 C方式（Voice 自己申告マーカー）の逆流を再現しないための分離という設計意図は維持する。
"""

from __future__ import annotations

from dataclasses import dataclass

# Gemini 呼びかけの合言葉（これだけで発火）。表記ゆれは実装判断で追加してよい。
EXPLICIT_ADVISOR_MARKERS: tuple[str, ...] = (
    "Gemini",
    "gemini",
    "ジェミニ",
)

# コード相談ドメイン（小文字比較用）。Gemini 呼びかけ時の tool 振り分けにのみ使う
# （独立した発火条件としては使わない）。
CODE_MARKERS: tuple[str, ...] = (
    "python",
    "ssl",
    "requests",
    "gas",
    "コード",
    "ライブラリ",
    "pip ",
    "証明書",
    "certificate",
    "traceback",
    "syntaxerror",
    "importerror",
    "モジュール",
    "apiに",
    "api に",
)


@dataclass(frozen=True)
class ForcedAdvisorPlan:
    """Core が強制する外聞き1件。"""

    tool: str  # web_search | code_qa
    query: str
    why: str


def _looks_like_code(utterance: str) -> bool:
    lower = utterance.lower()
    return any(m in lower for m in CODE_MARKERS)


def _has_any(utterance: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        if marker in utterance:
            return marker
    return None


def plan_forced_advisor(utterance: str) -> ForcedAdvisorPlan | None:
    """Gemini 呼びかけ（合言葉明示）があれば ForcedAdvisorPlan、なければ None。

    発火条件はこれ一つのみ（2026-07-31 改訂で合言葉ゼロの自動発火を全廃）。
    雑談・事実ドメイン発話は、合言葉が無ければ一切発火しない。
    """
    text = utterance.strip()
    if not text:
        return None

    advisor_hit = _has_any(text, EXPLICIT_ADVISOR_MARKERS)
    if advisor_hit is None:
        return None

    # コードっぽければ code_qa、それ以外（天気・一般含む）は web_search。
    tool = "code_qa" if _looks_like_code(text) else "web_search"
    return ForcedAdvisorPlan(
        tool=tool,
        query=text,
        why=f"規則: 明示アドバイザー「{advisor_hit}」",
    )
