"""python -m mind.memory_test --idea <イデアのフォルダー>"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mind.memory_test.cases import KINDS


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m mind.memory_test", description="今の記憶に問題集を解かせて採点する")
    parser.add_argument("--idea", help="イデアのフォルダー（省略すると NIRAI_IDEA）")
    parser.add_argument("--memory", choices=["legacy", "episodic"], default="legacy", help="解かせる記憶（今の記憶／作り直した記憶）")
    parser.add_argument("--kinds", nargs="*", choices=list(KINDS), help="この種類の問題だけ")
    parser.add_argument("--no-judge", action="store_true", help="「実際の会話」の関係の判定をしない")
    parser.add_argument("--no-planner-judge", action="store_true", help="想起の計画でGemmaに相談しない（規則だけ）")
    args = parser.parse_args()
    idea = Path(args.idea or os.environ.get("NIRAI_IDEA", "")).resolve()
    if not (idea / "identity.toml").is_file():
        sys.exit(f"{idea} はイデアのフォルダーではありません（identity.toml がありません）")

    from mind.memory_test.run import run

    run(
        idea,
        memory_name=args.memory,
        kinds=set(args.kinds) if args.kinds else None,
        judge=not args.no_judge,
        planner_judge=not args.no_planner_judge,
    )


if __name__ == "__main__":
    main()
