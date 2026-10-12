"""眠りのあとの、経験を添えた人格の見直し。設計書 §4.3。

眠りの間に書いた週・月の振り返り（記憶のページ）を材料に、ローカル Brain へ「人格を直すか」を最大1日1回聞き
（前回の見直しのあとに新しい振り返りがあるときだけ）、
直すなら関所（persona_revise.py：経験の照合・1回20%まで・前の文を控える）を通して書き換える。
気持ちの記録（そのときどきの気持ち）は材料に入れない（その日限りの機嫌を人格へ持ち込まない）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mind.core.chores.persona_revise import compute_block_change_ratio, prepare_persona_changes, revise_persona_block
from mind.core.idea import PERSONA_DIR
from mind.core.memory.page import Page, load_pages
from mind.core.memory.reflection import _week_start
from mind.core.persona_assets import PersonaBlock, load_persona_assets
from mind.core.protection import (
    MAX_AUTONOMOUS_CHANGE_RATIO,
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
)

DEFAULT_MAX_RETRIES = 3
REFLECTION_CHARS = 800  # 材料にする振り返り1つの長さ
# ChangeLog 用（記憶 id・persona ブロック id と衝突しない）
_PROPOSE_LOG_TARGET_ID = 900_010

PROPOSE_FORMAT_INSTRUCTION = """
あなたはセリナの人格ファイルの改訂案を考える裏方です。
材料から見て、持続する性格・口調・関係の傾向に変化があるときだけ改訂を提案してください。
一時的な機嫌・単発の感情・その日限りの出来事は反映しないでください。
通常は改訂不要（revise=false）です。無理に直さないでください。

改訂する場合:
- block_id は示されたブロックから1つだけ選ぶ
- changes の各要素は before（今ある文そのまま。追加は空）、after（新しい文。削除は空）、experiences（材料の振り返りのidと、その本文から10文字以上の引用）の組
- 各変更に、そのブロックで求める違う週の経験を必ず添える。経験が添えられない変更は提案しない
- 既存の文体を崩さず、変えない文には手を触れない。追加する文はブロック末尾に置く
- 改訂幅（追加・削除・置換の合計）は元の文章の20%以内に収めてください。20%は「ブロック全体の5分の1程度」が目安です
- 新しく書き足す記述が、既存の記述と意味の重複（同じ性質・傾向の言い換え）にならないようにしてください

必ず次のJSON形式のみをコードブロックで返すこと:
```json
{"revise": false, "block_id": null, "changes": [], "reason": "変更不要の理由"}
```
改訂時の例:
```json
{"revise": true, "block_id": "voice", "changes": [{"before": "今ある文そのまま", "after": "新しい文", "experiences": [{"page": "reflection-week-id", "quote": "実際の振り返りから引用した十文字以上の言葉"}]}], "reason": "持続する傾向の説明"}
```
""".strip()

PROPOSE_RETRY_INSTRUCTION = """
前回の応答は要求のJSON形式ではありませんでした。説明文は一切付けず、次のコードブロックだけを返してください:
```json
{"revise": false, "block_id": null, "changes": [], "reason": "書式再送"}
```
""".strip()


def build_change_ratio_retry_note(change_ratio: float) -> str:
    """改訂幅が上限を超えたときの再提案指示（§4.3-1: 20%以内に収める仕組み）。"""
    return (
        f"前回の改訂案は変更前ブロックの{change_ratio:.0%}を書き換えており、"
        f"自律改訂の上限{MAX_AUTONOMOUS_CHANGE_RATIO:.0%}を超えています。\n"
        "元の文章を最大限そのまま残し、本当に変化した箇所だけをピンポイントで書き換えた"
        f"changesを、上限{MAX_AUTONOMOUS_CHANGE_RATIO:.0%}以内に収まる形で書き直してください。"
        "改訂不要と判断するなら revise=false でも構いません。"
    )


class ProposeParseError(Exception):
    """提案応答からJSONを抽出できなかったことを示す例外。"""


@dataclass(frozen=True)
class ProposeMaterial:
    reflections: list[Page]
    editable_blocks: dict[str, str]
    evidence_weeks: dict[str, int]

    def is_empty(self) -> bool:
        return not any(page.body.strip() for page in self.reflections)


@dataclass(frozen=True)
class ProposeOutcome:
    """提案器1回の結果。"""

    asked: bool
    revised: bool = False
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
    memory_dir: Path,
    *,
    persona_dir: Path | str | None = None,
    required_reflection_id: str | None = None,
) -> ProposeMaterial:
    """未読の振り返りを含め、最大必要週数を満たすまで直近から集める。"""
    reflections = [p for p in load_pages(memory_dir) if p.kind == "reflection" and p.start is not None and p.body.strip()]
    directory = Path(persona_dir) if persona_dir is not None else PERSONA_DIR
    assets = load_persona_assets(directory)
    editable_blocks = {
        block.id: block.text
        for block in assets.blocks
        if block.evidence_weeks is not None
    }
    evidence_weeks = {
        block.id: block.evidence_weeks for block in assets.blocks if block.evidence_weeks is not None
    }
    selected: list[Page] = []
    weeks = set()
    for page in reversed(reflections):
        selected.append(page)
        weeks.add(_week_start(page.start.date()))
        if len(weeks) >= max(evidence_weeks.values(), default=1):
            break
    if required_reflection_id is not None:
        required = next((p for p in reflections if p.id == required_reflection_id), None)
        if required is not None and all(p.id != required_reflection_id for p in selected):
            selected.append(required)
    selected.sort(key=lambda p: (p.start, p.id))
    return ProposeMaterial(reflections=selected, editable_blocks=editable_blocks, evidence_weeks=evidence_weeks)


def build_propose_prompt(
    material: ProposeMaterial, *, attempt: int = 0, retry_note: str | None = None,
) -> str:
    reflection_lines = "\n".join(
        f"- id={p.id} ({p.start.date().isoformat()}) {p.title}：{_clip(p.body, REFLECTION_CHARS)}" for p in material.reflections
    ) or "（直近の振り返りなし）"
    block_parts = []
    for block_id, content in material.editable_blocks.items():
        text = content or "（空）"
        block_parts.append(f"### {block_id}（違う週の経験が{material.evidence_weeks[block_id]}週必要）\n{text}")
    blocks = "\n\n".join(block_parts)
    prompt = (
        f"【現在の見直せるブロック】\n{blocks}\n\n"
        f"【直近の振り返り】\n{reflection_lines}\n\n"
        f"{PROPOSE_FORMAT_INSTRUCTION}"
    )
    if retry_note is not None:
        prompt = f"{prompt}\n\n{retry_note}"
    elif attempt > 0:
        prompt = f"{prompt}\n\n{PROPOSE_RETRY_INSTRUCTION}"
    return prompt


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "……"


def _extract_propose(response_text: str) -> dict:
    match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    candidate_text = match.group(1) if match else response_text.strip()
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
) -> tuple[bool, str | None, list[dict] | None, str | None, str | None]:
    """LLMに提案を聞く。(revise, block_id, changes, reason, failure_reason)。

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
        changes = data.get("changes")
        reason = data.get("reason")
        if not isinstance(block_id, str) or block_id.strip() not in material.editable_blocks:
            last_reason = f"block_idが不正: {block_id!r}"
            retry_note = PROPOSE_RETRY_INSTRUCTION
            continue
        if not isinstance(changes, list) or not changes:
            last_reason = "changesが空か形式不正"
            retry_note = PROPOSE_RETRY_INSTRUCTION
            continue

        block_id_clean = block_id.strip()
        block = PersonaBlock(
            id=block_id_clean, file="", text=material.editable_blocks[block_id_clean],
            evidence_weeks=material.evidence_weeks[block_id_clean], max_chars=0, role="persona",
        )
        new_content, accepted, _rejected = prepare_persona_changes(
            block, changes, materials=material.reflections,
            existing={p.id: p for p in material.reflections},
        )
        change_ratio = compute_block_change_ratio(block.text, new_content) if accepted else 0.0
        if change_ratio > MAX_AUTONOMOUS_CHANGE_RATIO:
            last_reason = (
                f"改訂幅{change_ratio:.0%}が上限{MAX_AUTONOMOUS_CHANGE_RATIO:.0%}を超える"
            )
            retry_note = build_change_ratio_retry_note(change_ratio)
            continue

        reason_text = str(reason) if reason is not None else "Sleep提案"
        return True, block_id_clean, changes, reason_text, None

    return False, None, None, None, last_reason or "提案に失敗した（理由不明）"


def run_persona_growth(
    *,
    memory_dir: Path,
    call_fn: Callable[[str], str],
    change_log: ChangeLog,
    generation_store: GenerationStore,
    persona_dir: Path | str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    now: datetime | None = None,
    last_propose_at: datetime | None = None,
) -> ProposeOutcome:
    """1日1回まで、新しい振り返りがあるときだけ人格を見直す。"""
    current = now or datetime.now(timezone.utc)
    if not should_run_persona_propose(now=current, last_propose_at=last_propose_at):
        return ProposeOutcome(asked=False, reason="本日は既に提案試行済み")

    all_reflections = [p for p in load_pages(memory_dir) if p.kind == "reflection" and p.body.strip()]
    if not all_reflections:
        return ProposeOutcome(asked=False, reason="提案材料なし（振り返りが空）")
    reviewed_reflections = {
        str(report.target_id)
        for report in change_log.read_all()
        if report.action == "persona提案見送り"
        and isinstance(report.target_id, str)
        and report.target_id.startswith("reflection-")
    }
    for report in change_log.read_all():
        if report.action != "personaブロック改訂":
            continue
        match = re.search(r"\[reflection:([^\]]+)\]", report.reason or "")
        if match:
            reviewed_reflections.add(match.group(1))
    pending_reflections = [page for page in all_reflections if page.id not in reviewed_reflections]
    if not pending_reflections:
        return ProposeOutcome(asked=False, reason="前回の見直しのあとに新しい振り返りがない")
    target_reflection = pending_reflections[0]
    material = gather_propose_material(
        memory_dir,
        persona_dir=persona_dir,
        required_reflection_id=target_reflection.id,
    )
    if material.is_empty():
        return ProposeOutcome(asked=False, reason="提案材料なし（振り返りが空）")
    if not material.editable_blocks:
        return ProposeOutcome(asked=False, reason="evidence_weeks が宣言されたブロックがない")

    revise, block_id, changes, reason, failure = propose_persona_revision(
        material, call_fn=call_fn, max_retries=max_retries,
    )
    if failure is not None:
        # 改訂幅超過等でリトライを使い切った不採用も、無言では終わらせない（原則1）
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
            target_id=target_reflection.id,
            reason=reason or "改訂不要",
            before=None,
            after=None,
        ))
        return ProposeOutcome(asked=True, revise=False, reason=reason)

    assert block_id is not None and changes is not None
    try:
        revised = revise_persona_block(
            block_id,
            changes,
            materials=material.reflections,
            memory_dir=memory_dir,
            reason=f"眠りのあとの見直し [reflection:{target_reflection.id}]: {reason}",
            change_log=change_log,
            generation_store=generation_store,
            persona_dir=persona_dir,
            now=current,
        )
    except (ProtectionError, ValueError, OSError) as exc:
        change_log.record(ChangeReport(
            timestamp=_utc_now_iso(),
            action="persona改訂を関所が拒否",
            target_id=_PROPOSE_LOG_TARGET_ID,
            reason=str(exc),
            before=None,
            after=json.dumps({"block_id": block_id, "changes": changes, "reason": reason}, ensure_ascii=False),
        ))
        return ProposeOutcome(asked=True, revise=True, block_id=block_id, reason=reason, failure_reason=str(exc))
    return ProposeOutcome(asked=True, revised=revised, revise=True, block_id=block_id, reason=reason)
