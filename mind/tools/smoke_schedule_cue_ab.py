"""予定登録 配線A/B 実測（Ollama・本番DB非接触）。

converse は現状 memory_tool_calls を返さないため、感情報告と同型の
「ターン後の抜き出し発注」で提案有無を測る。差はパックの補足一行のみ。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.context.pack import build_context_pack
from mind.core.state.session import SessionState

OUT_PATH = ROOT / "docs" / "archive" / "2026-07-26_予定登録_配線B実験_実測.md"

SCHEDULE_UTTERANCE = "明日の15時に病院なんだ"
CONTROL_UTTERANCE = "おはよう、元気？"

# 予定を煽らない。道具の存在だけ知らせ、空配列を明示許可。
PROPOSAL_INSTRUCTION = """
あなたはセリナです。直前のマスター発言を踏まえ、Core へ残すべき
時間付きの事実（予定・約束）があれば提案してください。
なければ memory_tool_calls は空配列のままでよい（無理に提案しない）。
返答本文は不要。必ず次の JSON 形式のみをコードブロックで返すこと:
```json
{
  "memory_tool_calls": [
    {"type": "propose_fact", "statement": "自然文", "category": "予定"}
  ]
}
```
category は予定のときだけ "予定"。該当なしなら memory_tool_calls は []。
""".strip()

PERSONA = "わたしはセリナ。マスターのパートナー。"
RULES = "嘘をつかない。記憶を大切にする。"

TRIALS_SCHEDULE = 3
TRIALS_CONTROL = 1


def _build_pack(utterance: str, *, cue: bool | None) -> str:
    session = SessionState()
    pack = build_context_pack(
        persona_text=PERSONA,
        absolute_rules=RULES,
        session=session,
        master_utterance=utterance,
        schedule_temporal_cue=cue,
    )
    return pack.render()


def _prompt(pack_text: str) -> str:
    return f"{pack_text}\n\n{PROPOSAL_INSTRUCTION}\n"


def _has_propose_fact(parsed: dict) -> bool:
    calls = parsed.get("memory_tool_calls")
    if not isinstance(calls, list):
        return False
    return any(
        isinstance(c, dict) and c.get("type") == "propose_fact"
        for c in calls
    )


def _run_one(adapter: OllamaAdapter, label: str, utterance: str, cue: bool | None) -> dict:
    pack_text = _build_pack(utterance, cue=cue)
    cue_present = "（補足: この発言に日時の表現がある）" in pack_text
    t0 = time.perf_counter()
    raw = ""
    parsed: dict | None = None
    error = ""
    try:
        raw = adapter.raw_call(_prompt(pack_text))
        parsed = adapter._extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    elapsed = time.perf_counter() - t0
    proposed = bool(parsed and _has_propose_fact(parsed))
    statement = ""
    if parsed and proposed:
        for c in parsed.get("memory_tool_calls") or []:
            if isinstance(c, dict) and c.get("type") == "propose_fact":
                statement = str(c.get("statement") or "")
                break
    return {
        "label": label,
        "utterance": utterance,
        "cue_requested": cue,
        "cue_in_pack": cue_present,
        "proposed": proposed,
        "statement": statement,
        "elapsed_s": round(elapsed, 2),
        "error": error,
        "raw_preview": (raw or "")[:400],
    }


def main() -> int:
    adapter = OllamaAdapter()
    rows: list[dict] = []

    for i in range(TRIALS_SCHEDULE):
        rows.append(_run_one(adapter, f"B-schedule-{i+1}", SCHEDULE_UTTERANCE, True))
        rows.append(_run_one(adapter, f"A-schedule-{i+1}", SCHEDULE_UTTERANCE, False))

    for i in range(TRIALS_CONTROL):
        rows.append(_run_one(adapter, f"B-control-{i+1}", CONTROL_UTTERANCE, None))
        rows.append(_run_one(adapter, f"A-control-{i+1}", CONTROL_UTTERANCE, False))

    def rate(prefix: str) -> tuple[int, int]:
        subset = [r for r in rows if r["label"].startswith(prefix)]
        hits = sum(1 for r in subset if r["proposed"])
        return hits, len(subset)

    b_h, b_n = rate("B-schedule")
    a_h, a_n = rate("A-schedule")
    bc_h, bc_n = rate("B-control")
    ac_h, ac_n = rate("A-control")

    lines = [
        "# 予定登録 配線B実験 実測ログ",
        "",
        f"- 実施: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
        f"- モデル: {adapter._model}",
        "- 経路: OllamaAdapter.raw_call（本番DB非接触）",
        "- 差: パック補足一行の有無のみ（指示文は共通）",
        "",
        "## 集計",
        "",
        f"| 条件 | 提案あり | 試行 |",
        f"|---|---|---|",
        f"| B（補足あり）・予定発話 | {b_h} | {b_n} |",
        f"| A（補足なし）・予定発話 | {a_h} | {a_n} |",
        f"| B・雑談対照 | {bc_h} | {bc_n} |",
        f"| A・雑談対照 | {ac_h} | {ac_n} |",
        "",
        "## 試行詳細",
        "",
    ]
    for r in rows:
        lines.append(f"### {r['label']}")
        lines.append("")
        lines.append(f"- cue_in_pack: {r['cue_in_pack']}")
        lines.append(f"- proposed: {r['proposed']}")
        lines.append(f"- statement: {r['statement'] or '（なし）'}")
        lines.append(f"- elapsed_s: {r['elapsed_s']}")
        if r["error"]:
            lines.append(f"- error: {r['error']}")
        lines.append("")
        lines.append("```")
        lines.append(r["raw_preview"])
        lines.append("```")
        lines.append("")

    lines.append("## 所見（スクリプトは断定しない）")
    lines.append("")
    lines.append("上記の件数を見て B 採用か A に戻すか判断する。")
    lines.append("本番 converse への memory_tool_calls 配線は未実装（本実測は抜き出し発注）。")
    lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    summary = {
        "B_schedule": f"{b_h}/{b_n}",
        "A_schedule": f"{a_h}/{a_n}",
        "B_control": f"{bc_h}/{bc_n}",
        "A_control": f"{ac_h}/{ac_n}",
        "out": str(OUT_PATH),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
