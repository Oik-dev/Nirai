"""問題集を記憶に解かせて、採点する。

記憶（出来事のページと、ACT-Rの活性で思い出す）は、索引を読むだけ。想起の記録も書かないので、
本物のイデアの記憶は変わらない。結果は、イデアの data/memory_test/results/ に、問題のIDと数だけで残す。
旧い記憶（2026-10まで。記憶DB serina_memory.db）の点数は、計画書 §11 に基準点として残してある。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from mind.brains.ollama.ask_json import ask_json
from mind.core.memory.embedder import OllamaEmbedder
from mind.memory_test.cases import CaseSet
from mind.memory_test.episodic import EpisodicMemory
from mind.memory_test.judge import Judge
from mind.memory_test.memory import Cue, Memory
from mind.memory_test.question_set import assemble
from mind.memory_test.record import JST
from mind.memory_test.score import render, score_case, summarize


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
    kinds: set[str] | None = None,
    judge: bool = True,
    progress: Callable[[str], None] = print,
) -> dict:
    store = CaseSet.of_idea(idea)
    cases = [case for case in assemble(idea) if not kinds or case.kind in kinds]
    judge_with = Judge(store.judgments, ask_json) if judge else None
    memory = EpisodicMemory(idea, embed=OllamaEmbedder(request_timeout_seconds=120.0).embed)
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
                "options": {"kinds": sorted(kinds) if kinds else None, "judge": judge},
                "params": asdict(memory.recaller.params),
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
