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

from mind.core.chores.chore_box import ChoreBox
from mind.core.chores.diary import EPISODIC_MEMORY_TYPE
from mind.core.chores.persona_revise import (
    PERSONA_REVISE_CHORE_KIND,
    compute_block_change_ratio,
)
from mind.core.memory.diary_date import resolve_diary_target_date
from mind.core.memory.protection import (
    MAX_AUTONOMOUS_CHANGE_RATIO,
    ChangeLog,
    ChangeReport,
)
from mind.core.memory.store import MemoryRecord, MemoryStore
from mind.core.persona_assets import load_persona_assets
from mind.core.idea import PERSONA_DIR

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
- **元の文章のうち、今回変える必要のない記述は一字一句そのまま残してください。全部を書き直すのではなく、変化があった部分だけを最小限書き換える「部分編集」のつもりで書くこと**
- 改訂幅（追加・削除・置換の合計）は元の文章の20%以内に収めてください。20%は「ブロック全体の5分の1程度」が目安です
- 新しく書き足す記述が、既存の記述と意味の重複（同じ性質・傾向の言い換え）にならないようにしてください

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


def build_change_ratio_retry_note(change_ratio: float) -> str:
    """改訂幅が上限を超えたときの再提案指示（§4.3-1: 20%以内に収める仕組み）。"""
    return (
        f"前回の改訂案は変更前ブロックの{change_ratio:.0%}を書き換えており、"
        f"自律改訂の上限{MAX_AUTONOMOUS_CHANGE_RATIO:.0%}を超えています。\n"
        "元の文章を最大限そのまま残し、本当に変化した箇所だけをピンポイントで書き換えた"
        f"new_contentを、上限{MAX_AUTONOMOUS_CHANGE_RATIO:.0%}以内に収まる形で書き直してください。"
        "改訂不要と判断するなら revise=false でも構いません。"
    )


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
    diaries = memory_store.list_by_type(EPISODIC_MEMORY_TYPE, limit=max(1, diary_limit))
    directory = Path(persona_dir) if persona_dir is not None else PERSONA_DIR
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


def build_propose_prompt(
    material: ProposeMaterial, *, attempt: int = 0, retry_note: str | None = None,
) -> str:
    diary_lines = "\n".join(
        f"- ({resolve_diary_target_date(created_at=d.created_at, metadata=d.metadata)}) {d.content}"
        for d in material.diaries if d.content.strip()
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
    if retry_note is not None:
        prompt = f"{prompt}\n\n{retry_note}"
    elif attempt > 0:
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
    """LLMに提案を聞く。(revise, block_id, new_content, reason, failure_reason)。

    改訂幅が上限（§4.3-1・`MAX_AUTONOMOUS_CHANGE_RATIO`）を超えた場合も即失敗にせず、
    「小さい差分で書き直せ」という具体的な指示を添えてリトライ枠内で再挑戦させる
    （指示だけに頼らず、実測した変化率でフィードバックする仕組み）。
    """
    attempts = max(1, max_retries)
    last_reason: str | None = None
    retry_note: str | None = None
    for attempt in range(attempts):
        try:
            response_text = call_fn(
                build_propose_prompt(material, attempt=attempt, retry_note=retry_note),
            )
            data = _extract_propose(response_text)
            revise = _normalize_revise(data.get("revise"))
        except Exception as e:  # noqa: BLE001
            last_reason = f"{type(e).__name__}: {e}"
            retry_note = PROPOSE_RETRY_INSTRUCTION
            continue
        if revise is None:
            last_reason = f"reviseが不正: {data.get('revise')!r}"
            retry_note = PROPOSE_RETRY_INSTRUCTION
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
            retry_note = PROPOSE_RETRY_INSTRUCTION
            continue
        if not isinstance(new_content, str) or not new_content.strip():
            last_reason = "new_contentが空"
            retry_note = PROPOSE_RETRY_INSTRUCTION
            continue

        block_id_clean = block_id.strip()
        original_text = material.mutable_blocks.get(block_id_clean, "")
        change_ratio = compute_block_change_ratio(original_text, new_content)
        if change_ratio > MAX_AUTONOMOUS_CHANGE_RATIO:
            last_reason = (
                f"改訂幅{change_ratio:.0%}が上限{MAX_AUTONOMOUS_CHANGE_RATIO:.0%}を超える"
            )
            retry_note = build_change_ratio_retry_note(change_ratio)
            continue

        reason_text = str(reason) if reason is not None else "Sleep提案"
        return True, block_id_clean, new_content, reason_text, None

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
        # 2026-07-25是正: 改訂幅超過等でリトライを使い切った不採用も、無言では終わらせない
        # （原則1: 無言破棄の禁止。旧仕様は宿題箱経由の棚上げでレポートが残っていたが、
        # propose時点で弾くようになった分、ここで代わりに記録する）。
        change_log.record(ChangeReport(
            timestamp=_utc_now_iso(),
            action="persona提案不採用",
            target_id=_PROPOSE_LOG_TARGET_ID,
            reason=failure,
            before=None,
            after=None,
        ))
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
