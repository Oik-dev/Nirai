"""住人の記憶（イデアの memory/）を、記録から作る。docs/plans/長期記憶の作り直し.md の M3（写しのイデアで行う）。

    mind\\.venv\\Scripts\\python -m mind.tools.build_memory --idea <イデア> skeleton
    mind\\.venv\\Scripts\\python -m mind.tools.build_memory --idea <イデア> words [--limit N]
    mind\\.venv\\Scripts\\python -m mind.tools.build_memory --idea <イデア> index

- skeleton：整理の結果（memory/_seed/ の conversation・diary・notes の .jsonl）から、ページの骨組みを作る。
  最初の記憶づくりだけの段。整理は Claude が記録を読んで行った（2026-10-03、Masterの了承）。ページがすでにあれば何もしない。
- words：まだ本人の言葉がないページに、本人の脳（手元のGemma）で言葉を書き入れる。1ページ書くたびに保存するので、
  止めても次の回に続きから書く。
- index：記憶の索引（data/memory_index.db）を作り直す。埋め込みは bge-m3（CPU）。

M4で本物のイデアへ移したら、skeleton の段と _seed は消す（毎日の記憶づくりは、眠りの間に精神が行う）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from mind.brains.ollama.adapter import DEFAULT_MODEL
from mind.brains.ollama.ask_json import ask_json
from mind.core.idea import Idea
from mind.core.lifelog import read_conversation
from mind.core.memory.embedder import DEFAULT_MODEL as EMBED_MODEL
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.index import build_index
from mind.core.memory.page import load_pages, write_page
from mind.core.memory.structure import (
    MASTER,
    DiaryPart,
    EpisodeSpan,
    NoteRef,
    diary_pages,
    episode_evidence,
    episode_pages,
    link_neighbors,
    note_pages,
)
from mind.core.memory.writing import WordsRejected, write_episode, write_reread
from mind.core.persona_assets import load_persona_assets

STRUCTURED_BY = "claude-opus-5-5 (2026-10-03)"
RETRY_TEMPERATURE = 0.6  # 聞き直すときの揺らぎ（温度0のままでは、崩れた答えがそのまま繰り返される）


def _jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(raw) for raw in f if raw.strip()]


def _point(ref: str) -> tuple[str, int]:
    day, _, no = ref.partition("#")
    return day, int(no)


def skeleton(idea: Idea) -> None:
    if load_pages(idea.memory):
        print("ページがもうあるので、骨組みは作らない")
        return
    seed = idea.memory / "_seed"
    lines = read_conversation(idea.conversation)
    spans = [EpisodeSpan(_point(r["from"]), _point(r["to"]), tuple(r["concepts"])) for r in _jsonl(seed / "conversation.jsonl")]
    parts = [
        DiaryPart(
            file=r["file"],
            entry=r["entry"],
            paras=None if r["paras"] == "all" else tuple(int(n) for n in r["paras"].split("-")),
            concepts=tuple(r["concepts"]),
            at=datetime.fromisoformat(r["at"]) if "at" in r else None,
        )
        for r in _jsonl(seed / "diary.jsonl")
    ]
    notes = [NoteRef(r["source"], r["n"], tuple(r["concepts"])) for r in _jsonl(seed / "notes.jsonl")]
    pages = link_neighbors(
        episode_pages(lines, spans, structured_by=STRUCTURED_BY)
        + diary_pages(idea.legacy, parts, structured_by=STRUCTURED_BY)
        + note_pages(idea.legacy, notes, structured_by=STRUCTURED_BY)
    )
    for page in pages:
        write_page(idea.memory, page)
    kinds = {kind: sum(1 for p in pages if p.kind == kind) for kind in ("episode", "diary", "note")}
    print(f"骨組みを {len(pages)} ページ作った: {kinds}")


def words(idea: Idea, *, limit: int | None, model: str) -> None:
    persona = load_persona_assets(idea.persona).persona_text
    labels = {MASTER: "マスター", idea.name: "わたし"}
    lines = read_conversation(idea.conversation)
    written_by = f"{model} ({date.today().isoformat()})"
    pages = {page.id: page for page in load_pages(idea.memory)}
    todo = [page for page in pages.values() if not page.written]
    if limit is not None:
        todo = todo[:limit]
    print(f"本人の言葉がまだないページ: {sum(1 for p in pages.values() if not p.written)}（今回 {len(todo)}）", flush=True)
    def ask(prompt: str, schema: dict, attempt: int) -> dict:
        return ask_json(prompt, schema=schema, model=model, temperature=RETRY_TEMPERATURE if attempt else 0.0)

    failed = 0
    for n, page in enumerate(todo, start=1):
        began = time.monotonic()
        try:
            if page.kind == "episode":
                prev = pages.get(page.prev)
                done = write_episode(
                    page,
                    episode_evidence(lines, page, labels),
                    persona=persona,
                    labels=labels,
                    prev_title=prev.title if prev else "",
                    ask=ask,
                    written_by=written_by,
                )
            else:
                done = write_reread(page, persona=persona, ask=ask, written_by=written_by)
        except WordsRejected as e:
            failed += 1
            print(f"[{n}/{len(todo)}] {page.id} 書けなかった（次の回にもう一度）: {e}", flush=True)
            continue
        write_page(idea.memory, done)
        pages[done.id] = done
        print(f"[{n}/{len(todo)}] {page.id} {time.monotonic() - began:.0f}秒", flush=True)
    print(f"終わり。書けなかったページ: {failed}", flush=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m mind.tools.build_memory")
    parser.add_argument("--idea", required=True, help="イデアのフォルダー（M3では写しのイデア）")
    parser.add_argument("step", choices=["skeleton", "words", "index"])
    parser.add_argument("--limit", type=int, help="words：今回書くページの数")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="words：本人の脳（Ollamaのモデル）")
    args = parser.parse_args()
    idea = Idea.open(args.idea)
    if args.step == "skeleton":
        skeleton(idea)
    elif args.step == "words":
        words(idea, limit=args.limit, model=args.model)
    else:
        embedder = OllamaEmbedder(request_timeout_seconds=120.0)
        build_index(idea.memory, idea.conversation, idea.name, idea.memory_index, embed=embedder.embed, model=EMBED_MODEL)


if __name__ == "__main__":
    main()
