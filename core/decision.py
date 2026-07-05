"""① Decision（判断層）と ②→③ Evidence（行動結果の構造化）

北極星図の `Decision → Action → Evidence → Voice` のうち、Voice へ渡す前段を担う。

C1（配線）の原則:
- 判断は決定論・ルールベース。**気分・人格には一切依存しない**（逆流の壁）。
- 判断結果 `DecisionResult` は不変(frozen)。Voice が受け取っても書き換えられない。
- `action` / `query` は将来の検索復帰に備えて温存する（C1では未使用の受け口）。
- モデルは追加しない。実行担当の選択は既存のルール(router)を判断層へ昇格させて流用する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from serina.core.router import select
from serina.skills.base import Skill


@dataclass(frozen=True)
class DecisionResult:
    """今回どうするかの構造化された判断。人格そのものではなく、行動方針の器。"""

    intent: str  # 実行担当（既存 skill.name。例: "chat" / "distill"）
    action: str = "answer_from_knowledge"  # 将来: search / fetch / admit_unknown / ask_clarify
    query: str | None = None  # 検索復帰時の検索語（C1では常に None）
    confidence: str = "mid"  # high / mid / low


@dataclass(frozen=True)
class Evidence:
    """行動の結果＝Voice へ渡す根拠。成否だけでなく根拠・信頼度も含める。"""

    status: str  # 行動方針の結果（C1は decision.action をそのまま反映）
    source: str  # 出所ラベル（C1は "memory"）
    items: tuple[str, ...] = ()  # 根拠（C1は想起記憶の要約）
    confidence: str = "mid"
    error: str | None = None


def decide(user_input: str, skills: list[Skill], router=select) -> DecisionResult:
    """① 判断。ルールで実行担当を選び、構造化して返す。気分・人格に非依存。"""
    skill = router(user_input, skills)
    return DecisionResult(intent=skill.name)


def _summarize(mem: dict[str, Any]) -> str:
    return " ".join(str(mem.get("content", "")).split())


def build_evidence(decision: DecisionResult, memories: list[dict[str, Any]]) -> Evidence:
    """②→③ 行動結果を根拠として構造化する。C1は外部行動なし＝想起記憶を根拠にする。"""
    items = tuple(_summarize(m) for m in memories if _summarize(m))
    return Evidence(
        status=decision.action,
        source="memory",
        items=items,
        confidence=decision.confidence,
    )
