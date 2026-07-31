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
会話生成前の判定です。ウェブ検索が必要かだけをJSONで返してください。
```json
{"needs_search": false, "query": ""}
```
最新情報・固有の事実（天気・ニュース・価格・時事・特定の製品や出来事など）が
要る場合のみ true。雑談・感情の話・一般知識で答えられる話は false。
true のときは query に検索へ使う短い日本語クエリを入れる（発話全文をそのまま
使わなくてよい。固有名詞は言い換えず残すこと）。
"""


class _JudgeCapableBrain(Protocol):
    """判定発注に必要な最小面（persona 非注入の judge のみ）。"""

    def judge(self, prompt: str) -> dict: ...  # noqa: ANN001


@dataclass(frozen=True)
class TavilySearchDecision:
    needs_search: bool
    query: str
    why: str


def decide_tavily_search(
    master_utterance: str, brain: _JudgeCapableBrain
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

    prompt = f"{SEARCH_JUDGE_INSTRUCTION}\n\n【発話】\n{text}\n"
    try:
        result = judge(prompt)
        if not isinstance(result, dict):
            return TavilySearchDecision(
                needs_search=False, query="", why="judge応答が契約違反（dict以外）"
            )
        needs_search = bool(result.get("needs_search", False))
        query = str(result.get("query") or "").strip()
        if needs_search and not query:
            return TavilySearchDecision(
                needs_search=False, query="", why="judge契約違反（needs_search=trueなのにquery欠落）"
            )
        return TavilySearchDecision(
            needs_search=needs_search, query=query, why="judge判定"
        )
    except Exception:  # noqa: BLE001
        return TavilySearchDecision(needs_search=False, query="", why="judge例外")
