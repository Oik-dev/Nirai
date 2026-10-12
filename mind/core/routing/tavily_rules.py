"""Tavily 自律検索の要否判定（合言葉ゼロ・毎発話判定）。§3.7 / §5.6 改訂（2026-07-31）。

Voice（persona 注入の返答生成）に検索要否を委ねない。判定は Voice の生成呼び出しとは
完全に独立した Ollama 呼び出しで行う（persona 非注入）。マスター方針（合言葉ゼロ）により
`think_rules.py` のような規則層は持たず、常に judge へ委任する薄い関数のみを置く。

**判定へ供給してよい材料は `master_utterance`（原発話）のみに限る**。記憶想起・関係状態・
履歴等は供給しない。理由: この判定の出力（`query`）はそのまま外部（Tavily）へ送信される、
すなわち出力が外向きの境界を越える点で、出力が内部に留まる `think_rules`（深考判定）とは
性質が異なり、同じ緩さで供給材料を増やしてよい対象ではない
（実装前レビュー指摘・2026-07-31 改訂で明文化。本モジュールの関数シグネチャが
`master_utterance: str` 以外の会話文脈を受け取らないことでこれを強制する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

SEARCH_JUDGE_INSTRUCTION = """
会話生成前の判定です。検索の要否と、文ごとの『家に置く』印を同じ1回で判定してください。
```json
{"search": false, "home": []}
```
search は最新情報・固有の事実が必要な場合だけ true。
home は番号付きの各文のうち、Masterの心のデリケートな話またはクラウドの安全装置に
弾かれる踏み込んだ話を含む文の番号（1始まり）。番号を省いた文は外に出せる。
判断に迷う文はhomeに入れる。JSON以外は返さない。
"""

# 実運用で判明した不具合（2026-08-01）: judgeモデルは「固有名詞は残すこと」という指示を
# 字面どおり守り、マスターの呼びかけ語「セリナ」まで検索クエリへ持ち込んでしまうことがある
# （結果、検索エンジンへ「セリナ」というキーワードが混ざり、無関係な検索結果になる）。
# プロンプト指示だけに頼らず、機械的にも除去する（本プロジェクトの一貫方針: 指示は破られうる
# 前提で、外へ出る文字列は最後にCoreが機械的に整える）。
_SELF_REFERENCE_TERMS: tuple[str, ...] = ("セリナ",)


def _strip_self_reference(query: str) -> str:
    cleaned = query
    for term in _SELF_REFERENCE_TERMS:
        cleaned = cleaned.replace(term, " ")
    return " ".join(cleaned.split())


class _JudgeCapableBrain(Protocol):
    """判定発注に必要な最小面（persona 非注入の judge のみ）。"""

    def judge(self, prompt: str) -> dict: ...  # noqa: ANN001


@dataclass(frozen=True)
class TavilySearchDecision:
    needs_search: bool
    query: str
    why: str
    home_indices: tuple[int, ...] = ()
    home_valid: bool = False


def decide_tavily_search(
    master_utterance: str, brain: _JudgeCapableBrain, *, pieces: list[str] | None = None
) -> TavilySearchDecision:
    """検索要否を判定する。

    `judge` が呼べない／例外／契約違反時は `needs_search=False`（安全側・速度優先。
    既存の `_decide_deep_thinking` の「曖昧は false」踏襲）。
    """
    text = master_utterance.strip()
    if not text:
        return TavilySearchDecision(needs_search=False, query="", why="空発話")

    judge = getattr(brain, "judge", None)
    if not callable(judge):
        return TavilySearchDecision(needs_search=False, query="", why="judge未対応")

    numbered = pieces if pieces is not None else [text]
    lines = "\n".join(f"{i}. {part}" for i, part in enumerate(numbered, 1))
    prompt = f"{SEARCH_JUDGE_INSTRUCTION}\n\n【今回の発話】\n{text}\n\n【印を決める文】\n{lines}\n"
    try:
        result = judge(prompt)
        if not isinstance(result, dict) or type(result.get('search')) is not bool:
            return TavilySearchDecision(
                needs_search=False, query="", why="judge応答が契約違反（dict以外）"
            )
        home = result.get('home')
        if not isinstance(home, list) or any(type(n) is not int or n < 1 or n > len(numbered) for n in home):
            return TavilySearchDecision(False, '', '家の印の契約違反')
        needs_search = result['search']
        query = _strip_self_reference(text) if needs_search else ''
        return TavilySearchDecision(
            needs_search=bool(query), query=query, why="judge判定",
            home_indices=tuple(home), home_valid=True,
        )
    except Exception:  # noqa: BLE001
        return TavilySearchDecision(needs_search=False, query="", why="judge例外")
