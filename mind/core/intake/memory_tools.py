"""記憶ツール実行（Core 関所専用）。合意台帳 §3.2 / Wave 3 A6。

Brain は提案のみ。search/get は store 経由。propose_* は記録のみ（即 DB 書込禁止）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mind.core.memory.directed_forget import propose_forget_candidates
from mind.core.memory.recall_planner import MEMORY_TOOL_TYPES
from mind.core.memory.store import MemoryStore

MAX_TOOL_CALLS = 8


@dataclass
class MemoryToolOutcome:
    """記憶ツール実行結果（会話継続用。失敗は個別に記録）。"""

    executed: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)


def parse_memory_tool_calls(raw_calls: object) -> tuple[list[dict], list[dict]]:
    """lenient 解析: 壊れた要素は捨て、会話は続行。"""
    if not isinstance(raw_calls, list):
        return [], [{"reason": "memory_tool_calls が list でない"}]
    valid: list[dict] = []
    discarded: list[dict] = []
    for item in raw_calls[:MAX_TOOL_CALLS]:
        if not isinstance(item, dict):
            discarded.append({"reason": "要素が dict でない", "item": item})
            continue
        tool_type = item.get("type") or item.get("tool")
        if tool_type not in MEMORY_TOOL_TYPES:
            discarded.append({"reason": "未知のツール種別", "item": item})
            continue
        valid.append(item)
    return valid, discarded


def execute_memory_tool_calls(
    calls: list[dict],
    store: MemoryStore,
) -> MemoryToolOutcome:
    """記憶ツールを Core 関所で実行する。Brain から DB 直触り経路は作らない。"""
    outcome = MemoryToolOutcome()
    for call in calls:
        tool_type = call.get("type") or call.get("tool")
        try:
            if tool_type == "memory_search":
                query = call.get("query") or call.get("q") or ""
                if not isinstance(query, str) or not query.strip():
                    outcome.discarded.append({"tool": tool_type, "reason": "query が空"})
                    continue
                top_k = int(call.get("top_k", 5))
                records = store.recall(query.strip(), top_k=top_k)
                outcome.executed.append({
                    "tool": tool_type,
                    "query": query.strip(),
                    "results": [{"id": r.id, "content": r.content, "score": r.score} for r in records],
                })
            elif tool_type == "memory_get":
                memory_id = call.get("memory_id") or call.get("id")
                if memory_id is None:
                    outcome.discarded.append({"tool": tool_type, "reason": "memory_id 欠落"})
                    continue
                pair = store.get_memory_by_id(int(memory_id))
                if pair is None:
                    outcome.executed.append({"tool": tool_type, "memory_id": memory_id, "found": False})
                else:
                    record, pinned = pair
                    outcome.executed.append({
                        "tool": tool_type,
                        "memory_id": record.id,
                        "found": True,
                        "content": record.content,
                        "pinned": pinned,
                    })
            elif tool_type == "propose_fact":
                outcome.proposals.append({
                    "tool": tool_type,
                    "proposal": {k: v for k, v in call.items() if k not in ("type", "tool")},
                    "status": "pending_distillation",
                })
            elif tool_type == "propose_identity_edit":
                outcome.proposals.append({
                    "tool": tool_type,
                    "proposal": {k: v for k, v in call.items() if k not in ("type", "tool")},
                    "status": "pending_review",
                })
            elif tool_type == "forget_request":
                query = call.get("query") or call.get("q") or ""
                if not isinstance(query, str) or not query.strip():
                    outcome.discarded.append({"tool": tool_type, "reason": "query が空"})
                    continue
                candidates = propose_forget_candidates(store, query.strip(), top_k=int(call.get("top_k", 5)))
                outcome.proposals.append({
                    "tool": tool_type,
                    "query": query.strip(),
                    "candidates": [
                        {
                            "kind": c.kind,
                            "target_id": c.target_id,
                            "preview": c.preview,
                            "protection_grade": c.protection_grade,
                            "pinned": c.pinned,
                        }
                        for c in candidates
                    ],
                    "status": "awaiting_master_confirmation",
                })
        except Exception as exc:  # noqa: BLE001
            outcome.discarded.append({"tool": tool_type, "reason": str(exc), "call": call})
    return outcome
