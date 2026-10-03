"""python -m mind.memory_test make|run --idea <イデアのフォルダー>"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from mind.memory_test.cases import KINDS


def _idea(raw: str | None) -> Path:
    idea = Path(raw or os.environ.get("NIRAI_IDEA", "")).resolve()
    if not (idea / "identity.toml").is_file():
        sys.exit(f"{idea} はイデアのフォルダーではありません（identity.toml がありません）")
    return idea


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m mind.memory_test")
    sub = parser.add_subparsers(dest="command", required=True)
    make_p = sub.add_parser("make", help="問題集を作る（Gemmaが記録から問題を作る）")
    make_p.add_argument("--idea")
    make_p.add_argument("--no-gemma", action="store_true", help="Gemmaを呼ばず、下書きの控えだけで作る")
    run_p = sub.add_parser("run", help="今の記憶に問題集を解かせて採点する")
    run_p.add_argument("--idea")
    run_p.add_argument("--kinds", nargs="*", choices=list(KINDS), help="この種類の問題だけ")
    run_p.add_argument("--no-judge", action="store_true", help="「実際の会話」の関係の判定をしない")
    run_p.add_argument("--no-planner-judge", action="store_true", help="想起の計画でGemmaに相談しない（規則だけ）")
    args = parser.parse_args()
    idea = _idea(args.idea)
    if args.command == "make":
        from mind.memory_test.llm import ask_json
        from mind.memory_test.make import make

        cases = make(idea, ask=None if args.no_gemma else ask_json)
        counts = Counter(KINDS[case.kind] for case in cases)
        print("問題集: " + "、".join(f"{kind} {counts[kind]}" for kind in KINDS.values() if counts[kind]))
    else:
        from mind.memory_test.run import run

        run(
            idea,
            kinds=set(args.kinds) if args.kinds else None,
            judge=not args.no_judge,
            planner_judge=not args.no_planner_judge,
        )


if __name__ == "__main__":
    main()
