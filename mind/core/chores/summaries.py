"""常駐要約ブロック（prefs / relation）。合意台帳 §3.4 / §4-12 / Wave 4 A5, B8。

等級A・各800字以内・常駐合計3000字以内。裏方便のみが更新する（変更レポート必須）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mind.core.memory.facts import FACT_CATEGORY_PREFERENCE, FACT_CATEGORY_RELATIONSHIP, FactStore
from mind.core.memory.protection import ChangeLog, ChangeReport

MAX_BLOCK_CHARS = 800
MAX_TOTAL_CHARS = 3000

BLOCK_PREFS = "prefs_summary"
BLOCK_RELATION = "relation_summary"
SUMMARY_BLOCK_IDS = frozenset({BLOCK_PREFS, BLOCK_RELATION})

# ChangeLog target_id（記憶 id と衝突しない専用レンジ）
_TARGET_ID_PREFS = 800_001
_TARGET_ID_RELATION = 800_002
_TARGET_IDS = {BLOCK_PREFS: _TARGET_ID_PREFS, BLOCK_RELATION: _TARGET_ID_RELATION}

DEFAULT_SUMMARIES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "summaries"
DEFAULT_BLOCKS_PATH = DEFAULT_SUMMARIES_DIR / "blocks.json"

PREFS_SUMMARY_MARKER = "【好みの要約】"
RELATION_SUMMARY_MARKER = "【関係の要約】"


@dataclass(frozen=True)
class SummaryBlocks:
    prefs_summary: str
    relation_summary: str
    updated_at: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp_block(text: str, max_chars: int) -> str:
    """単一ブロックの字数上限を適用する。"""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1] + "…"


def enforce_summary_limits(prefs: str, relation: str) -> tuple[str, str]:
    """各800字・合計3000字のガード。超過時は畳み直す（§4-12）。"""
    prefs = clamp_block(prefs, MAX_BLOCK_CHARS)
    relation = clamp_block(relation, MAX_BLOCK_CHARS)
    total = len(prefs) + len(relation)
    if total <= MAX_TOTAL_CHARS:
        return prefs, relation
    if total == 0:
        return prefs, relation
    ratio = MAX_TOTAL_CHARS / total
    prefs_budget = int(len(prefs) * ratio)
    relation_budget = MAX_TOTAL_CHARS - prefs_budget
    return clamp_block(prefs, prefs_budget), clamp_block(relation, relation_budget)


def load_summary_blocks(path: Path | str | None = None) -> SummaryBlocks:
    """永続先から要約ブロックを読む。未作成時は空。"""
    blocks_path = Path(path) if path is not None else DEFAULT_BLOCKS_PATH
    if not blocks_path.exists():
        return SummaryBlocks(prefs_summary="", relation_summary="")
    raw = json.loads(blocks_path.read_text(encoding="utf-8"))
    prefs = raw.get(BLOCK_PREFS, {}).get("content", "")
    relation = raw.get(BLOCK_RELATION, {}).get("content", "")
    prefs, relation = enforce_summary_limits(prefs, relation)
    updated = raw.get("updated_at")
    return SummaryBlocks(prefs_summary=prefs, relation_summary=relation, updated_at=updated)


def _write_blocks(path: Path, prefs: str, relation: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _utc_now_iso(),
        BLOCK_PREFS: {"content": prefs},
        BLOCK_RELATION: {"content": relation},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_summary_block(
    block_id: str,
    new_content: str,
    *,
    change_log: ChangeLog,
    reason: str,
    path: Path | str | None = None,
) -> SummaryBlocks:
    """裏方便が要約ブロックを更新する（変更レポート必須）。"""
    if block_id not in SUMMARY_BLOCK_IDS:
        raise ValueError(f"未知の要約ブロック: {block_id}")

    blocks_path = Path(path) if path is not None else DEFAULT_BLOCKS_PATH
    current = load_summary_blocks(blocks_path)

    if block_id == BLOCK_PREFS:
        before = current.prefs_summary
        prefs, relation = enforce_summary_limits(new_content, current.relation_summary)
    else:
        before = current.relation_summary
        prefs, relation = enforce_summary_limits(current.prefs_summary, new_content)

    change_log.record(
        ChangeReport(
            timestamp=_utc_now_iso(),
            action="要約ブロック更新",
            target_id=_TARGET_IDS[block_id],
            reason=reason,
            before=before or None,
            after=prefs if block_id == BLOCK_PREFS else relation,
        )
    )
    _write_blocks(blocks_path, prefs, relation)
    return load_summary_blocks(blocks_path)


def rebuild_summaries_from_facts(
    fact_store: FactStore,
    *,
    change_log: ChangeLog,
    path: Path | str | None = None,
) -> SummaryBlocks:
    """facts台帳のactive fact（好み/関係性カテゴリ）からprefs_summary/relation_summaryを
    組み立てて反映する（2026-07-23。設計書決定事項5: persona提案の材料強化を兼ねる）。

    死んでいた自動更新配線の復活。内容に変化が無ければ何もしない（無駄な変更レポートを
    積まない。透明性原則は「変化があったとき必ず残す」であり「毎回残す」ではない）。
    """
    current = load_summary_blocks(path)

    preference_facts = fact_store.list_active_facts_by_category(FACT_CATEGORY_PREFERENCE)
    relationship_facts = fact_store.list_active_facts_by_category(FACT_CATEGORY_RELATIONSHIP)
    new_prefs = "\n".join(f.statement for f in preference_facts)
    new_relation = "\n".join(f.statement for f in relationship_facts)

    if new_prefs != current.prefs_summary:
        update_summary_block(
            BLOCK_PREFS,
            new_prefs,
            change_log=change_log,
            reason=f"facts台帳(好み)から再構築・active {len(preference_facts)}件",
            path=path,
        )
    if new_relation != current.relation_summary:
        update_summary_block(
            BLOCK_RELATION,
            new_relation,
            change_log=change_log,
            reason=f"facts台帳(関係性)から再構築・active {len(relationship_facts)}件",
            path=path,
        )
    return load_summary_blocks(path)


def render_summary_blocks_for_pack(blocks: SummaryBlocks) -> tuple[str, str]:
    """pack 常駐枠用。読み込み時点で字数ガード済みの文字列を返す。"""
    prefs, relation = enforce_summary_limits(blocks.prefs_summary, blocks.relation_summary)
    return prefs, relation
