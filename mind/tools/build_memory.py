"""住人の記憶（イデアの memory/）を、手で整える道具。ふだんは精神が眠りの間に行う（core/memory/sleep.py）。

    mind\\.venv\\Scripts\\python -m mind.tools.build_memory --idea <イデア> index
    mind\\.venv\\Scripts\\python -m mind.tools.build_memory --idea <イデア> sleep

- index：記憶の索引（data/memory_index.db）を、ページと記録から作り直す。埋め込みは bge-m3（CPU）。
  埋め込みのモデルを替えたときや、索引が壊れたときに使う。
- sleep：眠りを1回まわす（今の Serina 日より前の、まだページになっていない会話を、本人の脳でページにする）。
  写しのイデアで眠りを確かめるときに使う。気分の流れ（日記の材料）は読まないので、本物のイデアでは
  精神（Serina.bat）に眠らせること。

最初の記憶（2026-10-03）は、Claude が記録を読んで区切りと概念を決め（整理）、Serina の脳が言葉を書いた。
その整理の元（memory/_seed/）と、骨組みを作る段は、本物へ移したとき（M4）に役目を終えて消した（Git の履歴にある）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from mind.brains.ollama.adapter import DEFAULT_MODEL
from mind.brains.ollama.ask_json import asker
from mind.core.config import load_thresholds
from mind.core.idea import Idea
from mind.core.memory.embedder import DEFAULT_MODEL as EMBED_MODEL
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.memory import Memory
from mind.core.memory.sleep import sleep
from mind.core.persona_assets import load_persona_assets
from mind.core.state.serina_day import serina_day_id, serina_day_start


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m mind.tools.build_memory")
    parser.add_argument("--idea", required=True, help="イデアのフォルダー")
    parser.add_argument("step", choices=["index", "sleep"])
    parser.add_argument("--model", default=DEFAULT_MODEL, help="sleep：本人の脳（Ollamaのモデル）")
    args = parser.parse_args()
    idea = Idea.open(args.idea)
    embedder = OllamaEmbedder(request_timeout_seconds=120.0)
    memory = Memory(idea, embed=embedder.embed, embed_model=EMBED_MODEL)
    if args.step == "index":
        memory.rebuild_index()
        return
    now = datetime.now(timezone.utc)
    thresholds = load_thresholds()
    report = sleep(
        memory,
        before=serina_day_start(serina_day_id(now)),
        ask=asker(
            args.model,
            num_ctx=thresholds.ollama_num_ctx,
            use_mmap=thresholds.ollama_use_mmap,
        ),
        persona=load_persona_assets(idea.persona).persona_text,
        brain=args.model,
        today=serina_day_id(now),
        progress=lambda message: print(message, flush=True),
    )
    print(f"眠り終えた: 新しい出来事 {report.episodes}・書いたページ {report.written}・書けなかったページ {report.failed}")


if __name__ == "__main__":
    main()
