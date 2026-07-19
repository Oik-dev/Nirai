"""Sleep 側の persona 可変ブロック改訂提案器。設計書 §4.3 / §4.10。

セッション終了後の idle で、直近日記＋要約ブロックを材料にローカル Brain へ
「可変ブロックを直すか」を最大1日1回聞く。気分軌跡は材料に入れない（§4.3-2）。
改訂文は宿題箱 `persona改訂` に積み、適用は persona_revise.py の関所が行う。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.diary import DIARY_MEMORY_TYPE
from serina.core.chores.persona_revise import PERSONA_REVISE_CHORE_KIND
from serina.core.memory.protection import ChangeLog, ChangeReport
from serina.core.memory.store import MemoryRecord, MemoryStore
from serina.core.persona_assets import DEFAULT_PERSONA_DIR, load_persona_assets

MUTABLE_BLOCK_IDS = frozenset({"personality", "voice", "love"})
DEFAULT_DIARY_LIMIT = 3
DEFAULT_MAX_RETRIES = 3
# ChangeLog 用（記憶 id・persona ブロック id と衝突しない）
_PROPOSE_LOG_TARGET_ID = 900_010

PROPOSE_FORMAT_INSTRUCTION = """
あなたはセリナの人格ファイル（可変ブロック）の改訂案を考える裏方です。
材料から見て、持続する性格・口調・関係の傾向に変化があるときだけ改訂を提案してください。
一時的な機嫌・単発の感情・その日限りの出来事は反映しないでください。
通常は改訂不要（revise=false）です。無理に直さないでください。

改訂する場合:
- block_id は personality / voice / love のいずれか1つだけ
- new_content はそのブロックの改訂後全文（差分ではなく全文）
- 既存の文体・見出し構造を大きく崩さない

必ず次のJSON形式のみをコードブロックで返すこと:
```json
{"revise": false, "block_id": null, "new_content": null, "reason": "変更不要の理由"}
```
改訂時の例:
```json
{"revise": true, "block_id": "voice", "new_content": "（改訂後の全文）", "reason": "持続する傾向の説明"}
```
""".strip()

PROPOSE_RETRY_INSTRUCTION = """
前回の応答は要求のJSON形式ではありませんでした。説明文は一切付けず、次のコードブロックだけを返してください:
```json
{"revise": false, "block_id": null, "new_content": null, "reason": "書式再送"}
```
""".strip()


class ProposeParseError(Exception):
    """提案応答からJSONを抽出できなかったことを示す例外。"""


@dataclass(frozen=True)
class ProposeMaterial:
    diaries: list[MemoryRecord]
    prefs_summary: str
    relation_summary: str
    mutable_blocks: dict[str, str]

    def is_empty(self) -> bool:
        has_diary = any(d.content.strip() for d in self.diaries)
        has_summary = bool(self.prefs_summary.strip() or self.relation_summary.strip())
        return not has_diary and not has_summary


@dataclass(frozen=True)
class ProposeOutcome:
    """提案器1回の結果。"""

    asked: bool
    enqueued: bool = False
    revise: bool | None = None
    block_id: str | None = None
    reason: str | None = None
    failure_reason: str | None = None

    @property
    def advance_cooldown(self) -> bool:
        """LLMが解釈可能な応答を返したときだけ1日カウントを進める。"""
        return self.asked and self.failure_reason is None


def should_run_persona_propose(
    *,
    now: datetime,
    last_propose_at: datetime | None,
) -> bool:
    """ローカル暦日で「今日まだ聞いていない」なら True。"""
    if last_propose_at is None:
        return True
    return last_propose_at.astimezone().date() < now.astimezone().date()


def gather_propose_material(
    memory_store: MemoryStore,
    *,
    prefs_summary: str = "",
    relation_summary: str = "",
    persona_dir: Path | str | None = None,
    diary_limit: int = DEFAULT_DIARY_LIMIT,
) -> ProposeMaterial:
    """直近日記＋要約＋現在の可変ブロック本文を集める（気分軌跡は入れない）。"""
    diaries = memory_store.list_by_type(DIARY_MEMORY_TYPE, limit=max(1, diary_limit))
    directory = Path(persona_dir) if persona_dir is not None else DEFAULT_PERSONA_DIR
    assets = load_persona_assets(directory)
    mutable_blocks = {
        block.id: block.text
        for block in assets.blocks
        if block.id in MUTABLE_BLOCK_IDS and block.mutable
    }
    return ProposeMaterial(
        diaries=diaries,
        prefs_summary=prefs_summary or "",
        relation_summary=relation_summary or "",
        mutable_blocks=mutable_blocks,
    )


def build_propose_prompt(material: ProposeMaterial, *, attempt: int = 0) -> str:
    diary_lines = "\n".join(
        f"- ({d.created_at}) {d.content}" for d in material.diaries if d.content.strip()
    ) or "（直近日記なし）"
    prefs = material.prefs_summary.strip() or "（なし）"
    relation = material.relation_summary.strip() or "（なし）"
    block_parts = []
    for block_id in ("personality", "voice", "love"):
        text = material.mutable_blocks.get(block_id, "").strip() or "（空）"
        block_parts.append(f"### {block_id}\n{text}")
    blocks = "\n\n".join(block_parts)
    prompt = (
        f"【現在の可変ブロック】\n{blocks}\n\n"
        f"【直近の日記】\n{diary_lines}\n\n"
        f"【好みの要約】\n{prefs}\n\n"
        f"【関係の要約】\n{relation}\n\n"
        f"{PROPOSE_FORMAT_INSTRUCTION}"
    )
    if attempt > 0:
        prompt = f"{prompt}\n\n{PROPOSE_RETRY_INSTRUCTION}"
    return prompt


def _extract_propose(response_text: str) -> dict:
    match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if match:
        candidate_text = match.group(1)
    else:
        bare = re.search(r"\{[^{}]*\"revise\"[^{}]*\}", response_text, re.DOTALL)
        candidate_text = bare.group(0) if bare else response_text
    try:
        data = json.loads(candidate_text)
    except json.JSONDecodeError as e:
        raise ProposeParseError(f"提案応答からJSONを抽出できない: {e}") from e
    if "revise" not in data:
        raise ProposeParseError("reviseフィールドが無い")
    return data


def _normalize_revise(raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose_persona_revision(
    material: ProposeMaterial,
    *,
    call_fn: Callable[[str], str],
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[bool, str | None, str | None, str | None, str | None]:
    """LLMに提案を聞く。(revise, block_id, new_content, reason, failure_reason)。"""
    attempts = max(1, max_retries)
    last_reason: str | None = None
    for attempt in range(attempts):
        try:
            response_text = call_fn(build_propose_prompt(material, attempt=attempt))
            data = _extract_propose(response_text)
            revise = _normalize_revise(data.get("revise"))
        except Exception as e:  # noqa: BLE001
            last_reason = f"{type(e).__name__}: {e}"
            continue
        if revise is None:
            last_reason = f"reviseが不正: {data.get('revise')!r}"
            continue
        if not revise:
            reason = data.get("reason")
            reason_text = str(reason) if reason is not None else "改訂不要"
            return False, None, None, reason_text, None

        block_id = data.get("block_id")
        new_content = data.get("new_content")
        reason = data.get("reason")
        if not isinstance(block_id, str) or block_id.strip() not in MUTABLE_BLOCK_IDS:
            last_reason = f"block_idが不正: {block_id!r}"
            continue
        if not isinstance(new_content, str) or not new_content.strip():
            last_reason = "new_contentが空"
            continue
        reason_text = str(reason) if reason is not None else "Sleep提案"
        return True, block_id.strip(), new_content, reason_text, None

    return False, None, None, None, last_reason or "提案に失敗した（理由不明）"


def run_idle_persona_propose_chunk(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    call_fn: Callable[[str], str],
    change_log: ChangeLog,
    prefs_summary: str = "",
    relation_summary: str = "",
    persona_dir: Path | str | None = None,
    diary_limit: int = DEFAULT_DIARY_LIMIT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    now: datetime | None = None,
    last_propose_at: datetime | None = None,
    yield_check: Callable[[], bool] | None = None,
) -> ProposeOutcome:
    """②アイドル時: Sleep 提案を最大1回試行し、必要なら宿題箱へ積む。"""
    current = now or datetime.now(timezone.utc)
    if not should_run_persona_propose(now=current, last_propose_at=last_propose_at):
        return ProposeOutcome(asked=False, reason="本日は既に提案試行済み")
    if yield_check is not None and yield_check():
        return ProposeOutcome(asked=False, reason="会話再開のため中断")

    material = gather_propose_material(
        memory_store,
        prefs_summary=prefs_summary,
        relation_summary=relation_summary,
        persona_dir=persona_dir,
        diary_limit=diary_limit,
    )
    if material.is_empty():
        return ProposeOutcome(asked=False, reason="提案材料なし（日記・要約が空）")

    if yield_check is not None and yield_check():
        return ProposeOutcome(asked=False, reason="会話再開のため中断")

    revise, block_id, new_content, reason, failure = propose_persona_revision(
        material, call_fn=call_fn, max_retries=max_retries,
    )
    if failure is not None:
        return ProposeOutcome(asked=True, failure_reason=failure)

    if not revise:
        change_log.record(ChangeReport(
            timestamp=_utc_now_iso(),
            action="persona提案見送り",
            target_id=_PROPOSE_LOG_TARGET_ID,
            reason=reason or "改訂不要",
            before=None,
            after=None,
        ))
        return ProposeOutcome(asked=True, revise=False, reason=reason)

    assert block_id is not None and new_content is not None
    chore_box.enqueue(
        PERSONA_REVISE_CHORE_KIND,
        lane="local",
        payload={
            "block_id": block_id,
            "new_content": new_content,
            "reason": f"Sleep提案: {reason}",
            "mood_contaminated": False,
            "source": "sleep_propose",
        },
    )
    change_log.record(ChangeReport(
        timestamp=_utc_now_iso(),
        action="persona提案積込",
        target_id=_PROPOSE_LOG_TARGET_ID,
        reason=reason or "Sleep提案",
        before=None,
        after=json.dumps(
            {"block_id": block_id, "reason": reason},
            ensure_ascii=False,
        ),
    ))
    return ProposeOutcome(
        asked=True,
        enqueued=True,
        revise=True,
        block_id=block_id,
        reason=reason,
    )
