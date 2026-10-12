"""経験に裏付けられた文単位の人格改訂。設計書 §4.3。"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from mind.core.idea import PERSONA_DIR
from mind.core.lifelog import RecallLog
from mind.core.memory.page import Page, load_pages
from mind.core.memory.reflection import _week_start
from mind.core.persona_assets import PersonaBlock, load_persona_assets
from mind.core.protection import (
    MAX_AUTONOMOUS_CHANGE_RATIO,
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
    assert_persona_block_writable,
)

MIN_EXPERIENCE_QUOTE_CHARS = 10


def compute_block_change_ratio(before: str, after: str) -> float:
    """追加・削除・置換の合計÷変更前文字数。"""
    if not before:
        return 1.0 if after else 0.0
    delta = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=before, b=after).get_opcodes():
        if tag == "delete":
            delta += i2 - i1
        elif tag == "insert":
            delta += j2 - j1
        elif tag == "replace":
            delta += i2 - i1 + j2 - j1
    return min(1.0, delta / len(before))


def _quote_form(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def prepare_persona_changes(
    block: PersonaBlock,
    changes: object,
    *,
    materials: list[Page],
    existing: dict[str, Page],
) -> tuple[str, list[dict], list[dict]]:
    """変更ごとに現在の記憶と材料を照合。拒否した変更は本文・理由と共に返す。"""
    assert_persona_block_writable(evidence_weeks=block.evidence_weeks)
    allowed = {p.id: p for p in materials}
    result = block.text
    accepted: list[dict] = []
    rejected: list[dict] = []
    if not isinstance(changes, list):
        return result, accepted, [{"change": changes, "reason": "changesが配列でない"}]
    for change in changes:
        reason = ""
        if not isinstance(change, dict):
            reason = "変更が辞書でない"
        else:
            before, after, experiences = change.get("before"), change.get("after"), change.get("experiences")
            if not isinstance(before, str) or not isinstance(after, str) or before == after:
                reason = "before/afterが不正"
            elif not isinstance(experiences, list) or not experiences:
                reason = "経験の指定がない"
            elif before and (block.text.count(before) != 1 or result.count(before) != 1):
                reason = "beforeがブロックにちょうど1回ない"
            else:
                weeks = set()
                for evidence in experiences:
                    if not isinstance(evidence, dict) or not isinstance(evidence.get("page"), str) or not isinstance(evidence.get("quote"), str):
                        reason = "経験の形式が不正"
                        break
                    page_id, quote = evidence["page"], evidence["quote"]
                    page = allowed.get(page_id)
                    current = existing.get(page_id)
                    normalized = _quote_form(quote)
                    if page is None or current is None or current.kind != "reflection" or current.start is None:
                        reason = "経験のページが材料または現在の記憶にない"
                        break
                    if (len(normalized) < MIN_EXPERIENCE_QUOTE_CHARS
                            or normalized not in _quote_form(page.body)
                            or normalized not in _quote_form(current.body)):
                        reason = "引用が現在の振り返り本文にない、または短すぎる"
                        break
                    weeks.add(_week_start(current.start.date()))
                if not reason and len(weeks) < block.evidence_weeks:
                    reason = "違う週の経験が足りない"
        if reason:
            rejected.append({"change": change, "reason": reason})
            continue
        if before:
            result = result.replace(before, after, 1)
        else:
            result += after
        accepted.append({"before": before, "after": after, "experiences": experiences})
    return result, accepted, rejected


def revise_persona_block(
    block_id: str,
    changes: list[dict],
    *,
    materials: list[Page],
    memory_dir: Path,
    reason: str,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    persona_dir: Path | str | None = None,
    now: datetime | None = None,
) -> bool:
    """記憶を読み直して検証し、採用できた変更だけを原子的に反映する。"""
    directory = Path(persona_dir) if persona_dir is not None else PERSONA_DIR
    blocks = load_persona_assets(directory).blocks
    block = next((b for b in blocks if b.id == block_id), None)
    if block is None:
        raise ProtectionError(f"manifest に無いブロック id: {block_id}")
    existing = {p.id: p for p in load_pages(memory_dir)}
    new_content, accepted, rejected = prepare_persona_changes(block, changes, materials=materials, existing=existing)
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    for item in rejected:
        change_log.record(ChangeReport(
            timestamp=timestamp, action="persona変更不採用", target_id=block_id,
            reason=f"経験を添えられなかった: {item['reason']}", before=None,
            after=json.dumps(item["change"], ensure_ascii=False), changes=[item],
        ))
    if not accepted:
        return False
    ratio = compute_block_change_ratio(block.text, new_content)
    if ratio > MAX_AUTONOMOUS_CHANGE_RATIO:
        raise ProtectionError(f"改訂幅 {ratio:.0%} が上限 {MAX_AUTONOMOUS_CHANGE_RATIO:.0%} を超える")

    generation_store.save_persona_block(block_id, block.text, changes=accepted)
    block_path = directory / block.file
    tmp = block_path.with_name(block_path.name + ".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    os.replace(tmp, block_path)
    change_log.record(ChangeReport(
        timestamp=timestamp, action="personaブロック改訂", target_id=block_id,
        reason=reason, before=block.text, after=new_content, changes=accepted,
    ))
    # 採用されたページだけを1ページ1回、想起の強さへ反映する。
    recall = RecallLog(Path(memory_dir).parent / "lifelog" / "recall")
    pages = {e["page"] for change in accepted for e in change["experiences"]}
    for page_id in sorted(pages):
        recall.append(ts=now or datetime.now(timezone.utc), page=page_id, activation=0.0, vivid=False, intent="self")
    return True
