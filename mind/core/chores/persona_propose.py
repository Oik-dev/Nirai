"""眠りのあとの人格の見直し。1回1文、経験の関所と人格全文の照らし合わせ。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mind.core.chores.persona_revise import numbered_persona, revise_persona_block
from mind.core.config import load_thresholds
from mind.core.idea import PERSONA_DIR
from mind.core.memory.page import Page, load_pages
from mind.core.memory.reflection import _week_start
from mind.core.persona_assets import PersonaBlock, load_persona_assets
from mind.core.protection import ChangeLog, ChangeReport, GenerationStore, ProtectionError

DEFAULT_MAX_RETRIES = 3
REFLECTION_CHARS = 800
_PROPOSE_LOG_TARGET_ID = 900_010

PROPOSE_FORMAT_INSTRUCTION = """
あなたはセリナの人格の見直しを提案する裏方です。人格全文と週・月の振り返りから、
長く続く変化があるときだけ1回につき1文を提案してください。通常はrevise=falseです。
書いてよいのは、どんな場面でも効く傾向（性格・大事にしていること・話し方・愛し方）だけ。
出来事・事実・人のことは人格ではなく、記憶と関係の書き留めにあります。
一時的な気分や単発の出来事を人格に持ち込まないでください。

人格の文には通し番号があります。変更不可のブロックの文は選べません。
- 置き換え: targetに元の文番号、afterに新しい1文
- 追加: targetはnull、block_idに追加先、afterに新しい1文。ブロック末尾に置く
- 削除: targetに元の文番号、afterは空文字
- experiences: 材料の振り返りのidと、本文から引用した10文字以上の言葉を添える。
  変更先のブロックに必要な違う週の経験数を満たすこと。
- ほかの人格の文と矛盾・重複を生む変更は提案しないでください。

説明を付けず、JSONを1個だけ返してください。
変更なし: {"revise": false, "reason": "変化なし"}
変更あり: {"revise": true, "target": 12, "block_id": "voice", "after": "新しい1文。", "experiences": [{"page": "reflection-week-id", "quote": "振り返りから引用する十文字以上の言葉"}], "reason": "理由"}
""".strip()

PROPOSE_RETRY_INSTRUCTION = (
    'JSON形式が壊れていました。余分な説明を付けず、{"revise": false, "reason": "書式再送"} '
    "または指定の1文変更をJSON1個で返してください。"
)


class ProposeParseError(Exception):
    """脳のJSON形式が読めなかった。"""


@dataclass(frozen=True)
class ProposeMaterial:
    reflections: list[Page]
    blocks: tuple[PersonaBlock, ...]

    def is_empty(self) -> bool:
        return not any(page.body.strip() for page in self.reflections)


@dataclass(frozen=True)
class ProposeOutcome:
    asked: bool
    revised: bool = False
    revise: bool | None = None
    block_id: str | None = None
    reason: str | None = None
    failure_reason: str | None = None

    @property
    def advance_cooldown(self) -> bool:
        return self.asked and self.failure_reason is None


def should_run_persona_propose(*, now: datetime, last_propose_at: datetime | None) -> bool:
    """ローカル暦日で1日1回。"""
    return last_propose_at is None or last_propose_at.astimezone().date() < now.astimezone().date()


def gather_propose_material(
    memory_dir: Path,
    *,
    persona_dir: Path | str | None = None,
    required_reflection_id: str | None = None,
) -> ProposeMaterial:
    """未読を必ず含め、最大必要週数の違う週が揃うまで最近の振り返りを採る。"""
    reflections = [p for p in load_pages(memory_dir) if p.kind == "reflection" and p.start is not None and p.body.strip()]
    directory = Path(persona_dir) if persona_dir is not None else PERSONA_DIR
    blocks = load_persona_assets(directory).blocks
    required_weeks = max((b.evidence_weeks or 0 for b in blocks), default=1)
    selected: list[Page] = []
    weeks: set = set()
    for page in reversed(reflections):
        selected.append(page)
        weeks.add(_week_start(page.start.date()))
        if len(weeks) >= required_weeks:
            break
    if required_reflection_id is not None:
        required = next((p for p in reflections if p.id == required_reflection_id), None)
        if required is not None and all(p.id != required_reflection_id for p in selected):
            selected.append(required)
    selected.sort(key=lambda p: (p.start, p.id))
    return ProposeMaterial(reflections=selected, blocks=blocks)


def build_propose_prompt(material: ProposeMaterial, *, attempt: int = 0) -> str:
    reflections = "\n".join(
        f"- id={p.id} ({p.start.date().isoformat()}) {p.title}：{_clip(p.body, REFLECTION_CHARS)}"
        for p in material.reflections
    ) or "（直近の振り返りなし）"
    prompt = (
        f"【今の人格全文（変更不可も含む）】\n{numbered_persona(material.blocks)}\n\n"
        f"【週・月の振り返り】\n{reflections}\n\n{PROPOSE_FORMAT_INSTRUCTION}"
    )
    return prompt + (f"\n\n{PROPOSE_RETRY_INSTRUCTION}" if attempt else "")


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "……"


def _extract_propose(response_text: str) -> dict:
    match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    candidate = match.group(1) if match else response_text.strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ProposeParseError(f"提案応答からJSONを抽出できない: {exc}") from exc
    if not isinstance(data, dict) or type(data.get("revise")) is not bool:
        raise ProposeParseError("reviseが真偽値ではない")
    return data


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose_persona_revision(
    material: ProposeMaterial,
    *,
    call_fn: Callable[[str], str],
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[bool, dict | None, str | None, str | None]:
    """提案は1回（書式不良の再送だけ最大max_retries回）。"""
    last_reason: str | None = None
    for attempt in range(max(1, max_retries)):
        try:
            data = _extract_propose(call_fn(build_propose_prompt(material, attempt=attempt)))
        except Exception as exc:  # noqa: BLE001
            last_reason = f"{type(exc).__name__}: {exc}"
            continue
        reason = str(data.get("reason") or "改訂不要")
        if not data["revise"]:
            return False, None, reason, None
        return True, {k: v for k, v in data.items() if k not in ("revise", "reason")}, reason, None
    return False, None, None, last_reason or "提案の形式が不正"


def run_persona_growth(
    *,
    memory_dir: Path,
    call_fn: Callable[[str], str],
    change_log: ChangeLog,
    generation_store: GenerationStore,
    review_fn: Callable[[str], str] | None = None,
    context_window: int | None = None,
    persona_dir: Path | str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    now: datetime | None = None,
    last_propose_at: datetime | None = None,
) -> ProposeOutcome:
    """新しい週・月の振り返りがある日だけ1文を検討する。"""
    current = now or datetime.now(timezone.utc)
    if not should_run_persona_propose(now=current, last_propose_at=last_propose_at):
        return ProposeOutcome(asked=False, reason="本日は既に提案試行済み")
    all_reflections = [p for p in load_pages(memory_dir) if p.kind == "reflection" and p.body.strip()]
    if not all_reflections:
        return ProposeOutcome(asked=False, reason="提案材料なし（振り返りが空）")
    reports = change_log.read_all()
    reviewed = {
        str(r.target_id) for r in reports
        if r.action == "persona提案見送り" and isinstance(r.target_id, str) and r.target_id.startswith("reflection-")
    }
    for report in reports:
        if report.action == "personaブロック改訂":
            match = re.search(r"\[reflection:([^\]]+)\]", report.reason or "")
            if match:
                reviewed.add(match.group(1))
    pending = [p for p in all_reflections if p.id not in reviewed]
    if not pending:
        return ProposeOutcome(asked=False, reason="前回の見直しのあとに新しい振り返りがない")
    target_reflection = pending[0]
    material = gather_propose_material(memory_dir, persona_dir=persona_dir, required_reflection_id=target_reflection.id)
    if material.is_empty():
        return ProposeOutcome(asked=False, reason="提案材料なし（振り返りが空）")
    if not any(b.evidence_weeks for b in material.blocks):
        return ProposeOutcome(asked=False, reason="evidence_weeks が宣言されたブロックがない")

    revise, change, reason, failure = propose_persona_revision(material, call_fn=call_fn, max_retries=max_retries)
    if failure is not None:
        change_log.record(ChangeReport(
            timestamp=_utc_now_iso(), action="persona提案不採用", target_id=_PROPOSE_LOG_TARGET_ID,
            reason=failure, before=None, after=None,
        ))
        return ProposeOutcome(asked=True, failure_reason=failure)
    if not revise:
        change_log.record(ChangeReport(
            timestamp=_utc_now_iso(), action="persona提案見送り", target_id=target_reflection.id,
            reason=reason or "改訂不要", before=None, after=None,
        ))
        return ProposeOutcome(asked=True, revise=False, reason=reason)

    assert change is not None
    block_id = change.get("block_id") if isinstance(change.get("block_id"), str) else None
    try:
        revised = revise_persona_block(
            change, materials=material.reflections, memory_dir=memory_dir,
            review_fn=review_fn or call_fn,
            context_window=context_window if context_window is not None else load_thresholds().ollama_num_ctx,
            reason=f"眠りのあとの見直し [reflection:{target_reflection.id}]: {reason}",
            change_log=change_log, generation_store=generation_store,
            persona_dir=persona_dir, now=current,
        )
    except (ValueError, OSError, ProtectionError) as exc:
        change_log.record(ChangeReport(
            timestamp=_utc_now_iso(), action="persona改訂を関所が拒否", target_id=_PROPOSE_LOG_TARGET_ID,
            reason=str(exc), before=None, after=json.dumps(change, ensure_ascii=False),
        ))
        return ProposeOutcome(asked=True, revise=True, block_id=block_id, reason=reason, failure_reason=str(exc))
    if not revised:
        change_log.record(ChangeReport(
            timestamp=_utc_now_iso(), action="persona提案見送り", target_id=target_reflection.id,
            reason="提案された1文を確かめたが不採用", before=None, after=None,
        ))
    return ProposeOutcome(asked=True, revised=revised, revise=True, block_id=block_id, reason=reason)
