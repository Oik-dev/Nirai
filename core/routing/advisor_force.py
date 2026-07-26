"""事実レーンの決定論 backstop（§3.7 / §5.6）。

Voice（persona 注入の返答生成）に検索要否を委ねない。鮮度・明示検索・コード相談は
Core が規則で拾い、1通目は保留短文のみ・断定禁止 → 外聞き → 2通目で本体回答。
旧 C方式（Voice 自己申告マーカー）の逆流を再現しないための分離。
"""

from __future__ import annotations

from dataclasses import dataclass

# 1通目に出す固定保留（人格プロンプトには載せない。口調の調整はマスター側の別作業）。
FACT_LANE_HOLD_REPLY = "少し調べるね……"

# 明示の外聞き要求（これだけで発火）。
EXPLICIT_SEARCH_MARKERS: tuple[str, ...] = (
    "検索して",
    "検索してくれる",
    "ネットで検索",
    "ネットで調べ",
    "ググって",
    "調べて教えて",
    "調べてくれる",
    "調べてくれる？",
    "調べてくれるか",
)

EXPLICIT_ADVISOR_MARKERS: tuple[str, ...] = (
    "アドバイザー",
    "アドバイザに",
    "外に聞",
    "アドバイザーに聞",
)

# 鮮度語（単独では発火しない。FACT_DOMAIN または明示検索と組む）。
FRESHNESS_MARKERS: tuple[str, ...] = (
    "今日",
    "明日",
    "今週",
    "最新",
    "いまの",
    "今の",
    "現在の",
    "ただいま",
)

# 外部事実ドメイン（天気・相場など。訓練知識で答えさせない対象）。
FACT_DOMAIN_MARKERS: tuple[str, ...] = (
    "天気",
    "気温",
    "降水",
    "台風",
    "ニュース",
    "株価",
    "為替",
    "円相場",
    "仮想通貨",
    "ビットコイン",
    "BTC",
)

QUESTION_MARKERS: tuple[str, ...] = (
    "？",
    "?",
    "教えて",
    "どうなって",
    "いくら",
    "どのくらい",
    "なに",
    "何",
)

# コード相談ドメイン（小文字比較用）。
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
    """発話が事実レーンなら ForcedAdvisorPlan、そうでなければ None。

    優先順: 明示アドバイザー／コード → 明示検索 → 鮮度×ドメイン（＋質問サイン）。
    雑談の「今日も調子どう？」はドメインが無いので発火しない。
    """
    text = utterance.strip()
    if not text:
        return None

    advisor_hit = _has_any(text, EXPLICIT_ADVISOR_MARKERS)
    if advisor_hit is not None:
        # コードっぽければ code_qa、それ以外（天気・一般含む）は web_search。
        tool = "code_qa" if _looks_like_code(text) else "web_search"
        return ForcedAdvisorPlan(
            tool=tool,
            query=text,
            why=f"規則: 明示アドバイザー「{advisor_hit}」",
        )

    if _looks_like_code(text) and _has_any(text, QUESTION_MARKERS) is not None:
        return ForcedAdvisorPlan(
            tool="code_qa",
            query=text,
            why="規則: コードドメイン＋質問サイン",
        )

    search_hit = _has_any(text, EXPLICIT_SEARCH_MARKERS)
    if search_hit is not None:
        tool = "code_qa" if _looks_like_code(text) else "web_search"
        return ForcedAdvisorPlan(
            tool=tool,
            query=text,
            why=f"規則: 明示検索「{search_hit}」",
        )

    domain_hit = _has_any(text, FACT_DOMAIN_MARKERS)
    if domain_hit is None:
        return None
    fresh_hit = _has_any(text, FRESHNESS_MARKERS)
    question_hit = _has_any(text, QUESTION_MARKERS)
    if fresh_hit is None and question_hit is None:
        return None
    reason_bits = [f"ドメイン「{domain_hit}」"]
    if fresh_hit:
        reason_bits.append(f"鮮度「{fresh_hit}」")
    if question_hit:
        reason_bits.append(f"質問「{question_hit}」")
    return ForcedAdvisorPlan(
        tool="web_search",
        query=text,
        why="規則: " + "＋".join(reason_bits),
    )
