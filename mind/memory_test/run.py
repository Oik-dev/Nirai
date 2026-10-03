"""問題集を記憶に解かせて、採点する。

今の記憶（legacy）は、記憶DBを一時フォルダーへ写してから使う。作り直した記憶（episodic）は、索引を読むだけ。
どちらも本物のイデアの記憶は書き換えない。結果は、イデアの data/memory_test/results/ に、問題のIDと数だけで残す。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from mind.brains.ollama.ask_json import ask_json
from mind.core.memory.embedder import OllamaEmbedder
from mind.memory_test.cases import CaseSet
from mind.memory_test.episodic import EpisodicMemory
from mind.memory_test.judge import Judge
from mind.memory_test.legacy import LegacyMemory
from mind.memory_test.memory import Cue, Memory
from mind.memory_test.question_set import assemble
from mind.memory_test.record import JST
from mind.memory_test.score import render, score_case, summarize


def _copy_db(source: Path, target: Path) -> None:
    """SQLiteのバックアップで写す（書き込み途中の分も含めて、一貫した写しになる）。"""
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def solve(memory: Memory, cases, judge: Judge | None, *, progress: Callable[[str], None] = print):  # noqa: ANN001
    results = []
    for n, case in enumerate(cases, start=1):
        recalled = memory.recall(Cue(case.utterance, case.recent, case.now_at))
        relevance = None
        if case.kind == "replay" and judge is not None:
            relevance = judge.relevance(case, recalled) if recalled else []
        results.append(score_case(case, recalled, relevance))
        if n % 25 == 0 or n == len(cases):
            progress(f"  {n}/{len(cases)}")
    return results


def run(
    idea: Path,
    *,
    memory_name: str = "legacy",
    kinds: set[str] | None = None,
    judge: bool = True,
    planner_judge: bool = True,
    progress: Callable[[str], None] = print,
) -> dict:
    store = CaseSet.of_idea(idea)
    cases = [case for case in assemble(idea) if not kinds or case.kind in kinds]
    judge_with = Judge(store.judgments, ask_json) if judge else None
    if memory_name == "episodic":
        memory = EpisodicMemory(idea, embed=OllamaEmbedder(request_timeout_seconds=120.0).embed)
        results = solve(memory, cases, judge_with, progress=progress)
    else:
        with tempfile.TemporaryDirectory(prefix="nirai-memory-test-") as tmp:
            db = Path(tmp) / "serina_memory.db"
            _copy_db(idea / "data" / "serina_memory.db", db)
            memory = LegacyMemory(db, planner_judge=ask_json if planner_judge else None)
            results = solve(memory, cases, judge_with, progress=progress)
    summary = summarize(results)
    at = datetime.now(JST)
    store.results.mkdir(parents=True, exist_ok=True)
    out = store.results / f"{at:%Y-%m-%d_%H%M}_{memory.name}.json"
    out.write_text(
        json.dumps(
            {
                "memory": memory.name,
                "at": at.isoformat(),
                "options": {"kinds": sorted(kinds) if kinds else None, "judge": judge, "planner_judge": planner_judge},
                "params": asdict(memory.recaller.params) if memory_name == "episodic" else None,
                "summary": summary,
                "per_case": [asdict(result) for result in results],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    progress(render(summary))
    progress(f"結果: {out}")
    return summary
