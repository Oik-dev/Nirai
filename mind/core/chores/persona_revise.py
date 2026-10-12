"""経験に裏付けられ、人格全体と照らし合わされた1文だけを変える。設計書 §4.3。"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from mind.core.idea import PERSONA_DIR
from mind.core.lifelog import RecallLog
from mind.core.memory.page import Page, load_pages
from mind.core.memory.reflection import _week_start
from mind.core.persona_assets import PersonaBlock, load_persona_assets
from mind.core.protection import (
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
    assert_persona_block_writable,
)

MIN_EXPERIENCE_QUOTE_CHARS = 10
_SENTENCE = re.compile(r"[^。！？\r\n]+[。！？]?")


@dataclass(frozen=True)
class PersonaSentence:
    number: int
    block_id: str
    start: int
    end: int
    text: str


def persona_sentences(blocks: tuple[PersonaBlock, ...]) -> tuple[PersonaSentence, ...]:
    """区切りの文字を保持し、番号から元のブロック中の位置へ戻せる。"""
    out: list[PersonaSentence] = []
    for block in blocks:
        for match in _SENTENCE.finditer(block.text):
            if match.group().strip():
                out.append(PersonaSentence(len(out) + 1, block.id, match.start(), match.end(), match.group()))
    return tuple(out)


def numbered_persona(blocks: tuple[PersonaBlock, ...], *, marked: int | None = None) -> str:
    """manifest順で、不変ブロックも含めて全文を番号付きで見せる。"""
    sentences = persona_sentences(blocks)
    lines: list[str] = []
    for block in blocks:
        requirement = f"違う週の経験が{block.evidence_weeks}週必要" if block.evidence_weeks else "変更不可"
        lines.append(f"### {block.id}（{requirement}）")
        for sentence in sentences:
            if sentence.block_id == block.id:
                mark = "【今回の新しい文】" if marked == sentence.number else ""
                lines.append(f"{sentence.number}. {mark}{sentence.text}")
    return "\n".join(lines)


def _quote_form(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _json_answer(text: str) -> dict:
    candidate = text.strip()
    match = re.search(r"```json\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if match:
        candidate = match.group(1)
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("JSON objectがない")
    return value


@dataclass(frozen=True)
class PersonaChange:
    block_id: str
    before: str
    after: str
    new_content: str
    new_blocks: tuple[PersonaBlock, ...]
    marked_number: int | None
    experiences: list[dict]
    target: int | None


class ReviewRejected(ProtectionError):
    """照らし合わせが閉じた理由と、実際に届いた答えを残す。"""

    def __init__(self, reason: str, review: object) -> None:
        super().__init__(reason)
        self.review = review


def prepare_persona_change(
    blocks: tuple[PersonaBlock, ...],
    change: object,
    *,
    materials: list[Page],
    existing: dict[str, Page],
    context_window: int,
) -> PersonaChange:
    """入口の確かめ。壊れた答え・経験不足・番号違い・重すぎる変更は閉じる。"""
    if not isinstance(change, dict):
        raise ProtectionError("変更がJSON objectでない")
    target = change.get("target")
    after = change.get("after")
    supplied_id = change.get("block_id")
    if not isinstance(after, str):
        raise ProtectionError("afterが文字列でない")
    numbered = persona_sentences(blocks)
    if target is not None:
        if type(target) is not int or not (1 <= target <= len(numbered)):
            raise ProtectionError("targetの文番号が不正")
        sentence = numbered[target - 1]
        block_id, before = sentence.block_id, sentence.text
        if supplied_id is not None and supplied_id != block_id:
            raise ProtectionError("targetとblock_idが一致しない")
    else:
        if not isinstance(supplied_id, str) or not supplied_id:
            raise ProtectionError("追加先のblock_idがない")
        block_id, before, sentence = supplied_id, "", None
        if not after:
            raise ProtectionError("追加のafterが空")
    block = next((b for b in blocks if b.id == block_id), None)
    if block is None:
        raise ProtectionError("manifestにないブロック")
    if block.id in ("identity", "boundary") or block.role == "absolute_rules":
        raise ProtectionError("同一性事実と境界は自律変更しない")
    assert_persona_block_writable(evidence_weeks=block.evidence_weeks)
    if after and (len(list(_SENTENCE.finditer(after))) != 1
                  or not list(_SENTENCE.finditer(after))[0].group() == after
                  or "\n" in after or "\r" in after):
        raise ProtectionError("afterが1文ではない")
    if before == after:
        raise ProtectionError("変更前と変更後が同じ")

    experiences = change.get("experiences")
    if not isinstance(experiences, list) or not experiences:
        raise ProtectionError("経験の指定がない")
    allowed = {p.id: p for p in materials}
    weeks = set()
    for evidence in experiences:
        if not isinstance(evidence, dict) or not isinstance(evidence.get("page"), str) or not isinstance(evidence.get("quote"), str):
            raise ProtectionError("経験の形式が不正")
        page_id, quote = evidence["page"], evidence["quote"]
        page = allowed.get(page_id)
        current = existing.get(page_id)
        if page is None or current is None or current.kind != "reflection" or current.start is None:
            raise ProtectionError("経験のページが今回の材料または現在の記憶にない")
        normalized = _quote_form(quote)
        if (len(normalized) < MIN_EXPERIENCE_QUOTE_CHARS
                or normalized not in _quote_form(page.body)
                or normalized not in _quote_form(current.body)):
            raise ProtectionError("引用が振り返りの本文にない、または10文字より短い")
        weeks.add(_week_start(current.start.date()))
    if len(weeks) < block.evidence_weeks:
        raise ProtectionError("違う週の経験が足りない")

    if sentence is None:
        prefix = "" if not block.text or block.text.endswith("\n") else "\n"
        new_content = block.text + prefix + after
    else:
        new_content = block.text[:sentence.start] + after + block.text[sentence.end:]
    # 句点を欠いた置換が隣の文を飲み込むと、以後の文番号と全体照合が壊れる。
    # 編集前後で増減する文は必ず意図した1文だけにする。
    before_sentences = persona_sentences((block,))
    after_sentences = persona_sentences((replace(block, text=new_content),))
    expected_count = len(before_sentences) + (1 if sentence is None else -1 if not after else 0)
    if len(after_sentences) != expected_count:
        raise ProtectionError("変更が隣の文と結合または分割される")
    if after:
        changed_sentence = after_sentences[-1] if sentence is None else after_sentences[
            next(i for i, s in enumerate(before_sentences) if s.start == sentence.start and s.end == sentence.end)
        ]
        if changed_sentence.text != after:
            raise ProtectionError("変更後の文が隣の文と混ざる")
    new_blocks = tuple(replace(b, text=new_content) if b.id == block_id else b for b in blocks)
    before_chars = sum(len(b.text) for b in blocks)
    after_chars = sum(len(b.text) for b in new_blocks)
    ceiling = max(0, context_window // 4)
    if after_chars > ceiling and after_chars >= before_chars:
        raise ProtectionError(f"人格全体の重さが天井{ceiling}字を超える変更")
    mark = None
    if after:
        mark = target if target is not None else next(
            s.number for s in persona_sentences(new_blocks)[::-1] if s.block_id == block_id
        )
    return PersonaChange(block_id, before, after, new_content, new_blocks, mark, experiences, target)


def review_persona_change(candidate: PersonaChange, *, review_fn: Callable[[str], str]) -> dict:
    """追加・置換だけをGemmaで1回照合。壊れた答えは採らない。"""
    if not candidate.after:
        return {"skipped": "削除のみ"}
    prompt = (
        "あなたは人格を持たない整合性の照合係です。以下は変更を仮に当てた人格の全文です。"
        "【今回の新しい文】をほかの文と照らし合わせ、食い違い・同じことの言い直しがある番号を挙げてください。"
        "変更前の文はこの全文に含まれていないので、数えません。"
        "出来事・事実・人の説明ならmemory_like=true、言葉や振る舞いに効くならaffects_behavior=true、"
        "1つの傾向だけならsingle_trait=trueです。説明は付けず、JSONだけを返してください。\n"
        '{"conflicts": [], "duplicates": [], "memory_like": false, "affects_behavior": true, "single_trait": true}\n\n'
        f"【変更後の人格全文】\n{numbered_persona(candidate.new_blocks, marked=candidate.marked_number)}"
    )
    response: str | None = None
    try:
        response = review_fn(prompt)
        result = _json_answer(response)
    except Exception as exc:  # noqa: BLE001
        raise ReviewRejected(f"照らし合わせの答えを読めない: {type(exc).__name__}", response) from exc
    if (not isinstance(result.get("conflicts"), list)
            or not isinstance(result.get("duplicates"), list)
            or any(type(n) is not int for n in result["conflicts"] + result["duplicates"])
            or any(type(result.get(field)) is not bool for field in ("memory_like", "affects_behavior", "single_trait"))):
        raise ReviewRejected("照らし合わせの答えの形式が不正", result)
    if (result["conflicts"] or result["duplicates"] or result["memory_like"]
            or not result["affects_behavior"] or not result["single_trait"]):
        raise ReviewRejected("照らし合わせが不採用と判断", result)
    return result


def revise_persona_block(
    change: dict,
    *,
    materials: list[Page],
    memory_dir: Path,
    reason: str,
    review_fn: Callable[[str], str],
    context_window: int,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    persona_dir: Path | str | None = None,
    now: datetime | None = None,
) -> bool:
    """最新ページを照合し、1文だけを原子的に変更。失敗した変更も追記する。"""
    directory = Path(persona_dir) if persona_dir is not None else PERSONA_DIR
    blocks = load_persona_assets(directory).blocks
    existing = {p.id: p for p in load_pages(memory_dir)}
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    candidate: PersonaChange | None = None
    review: dict | None = None
    try:
        candidate = prepare_persona_change(blocks, change, materials=materials,
                                           existing=existing, context_window=context_window)
        review = review_persona_change(candidate, review_fn=review_fn)
    except ProtectionError as exc:
        item = {"change": change, "reason": str(exc)}
        if isinstance(exc, ReviewRejected):
            item["review"] = exc.review
        change_log.record(ChangeReport(
            timestamp=timestamp, action="persona変更不採用",
            target_id=str(change.get("block_id") or change.get("target") or "persona"),
            reason=str(exc), before=None, after=json.dumps(change, ensure_ascii=False), changes=[item],
        ))
        return False
    assert candidate is not None
    record = {"target": candidate.target, "before": candidate.before, "after": candidate.after,
              "experiences": candidate.experiences, "review": review}
    generation_store.save_persona_block(candidate.block_id, next(b.text for b in blocks if b.id == candidate.block_id),
                                        changes=[record])
    block = next(b for b in blocks if b.id == candidate.block_id)
    path = directory / block.file
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(candidate.new_content, encoding="utf-8")
    os.replace(tmp, path)
    change_log.record(ChangeReport(
        timestamp=timestamp, action="personaブロック改訂", target_id=candidate.block_id,
        reason=reason, before=block.text, after=candidate.new_content, changes=[record],
    ))
    recall = RecallLog(Path(memory_dir).parent / "lifelog" / "recall")
    for page_id in sorted({e["page"] for e in candidate.experiences}):
        recall.append(ts=now or datetime.now(timezone.utc), page=page_id, activation=0.0, vivid=False, intent="self")
    return True
