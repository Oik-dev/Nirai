"""ドリフト監査レポート（4c）: 自己像の変遷／beliefの増減／metricsの推移を日本語で出力

consolidation_log が原簿。Opusが定期レビューし、記憶汚染・執着ループ・キャラ変質を早期検知する。
読み取り専用（DBを変更しない・Ollama不要）。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.memory.db import get_connection


def main() -> None:
    conn = get_connection()
    print("=" * 60)
    print("Serina 成長・ドリフト監査レポート")
    print("=" * 60)

    print("\n【自己像の変遷】（新しい順）")
    rows = conn.execute(
        "SELECT ts, old_value, new_value FROM consolidation_log "
        "WHERE kind = 'self_image' ORDER BY ts DESC LIMIT 10"
    ).fetchall()
    if not rows:
        print("  （まだ自己像の更新はありません）")
    for r in rows:
        print(f"  [{r['ts'][:10]}] {r['new_value']}")
        if r["old_value"]:
            print(f"      ← 旧: {r['old_value']}")

    print("\n【信念（belief）】")
    n_belief = conn.execute(
        "SELECT COUNT(*) AS c FROM memories WHERE type = 'belief'").fetchone()["c"]
    kinds = conn.execute(
        "SELECT kind, COUNT(*) AS c FROM consolidation_log GROUP BY kind").fetchall()
    kind_map = {r["kind"]: r["c"] for r in kinds}
    print(f"  現在の信念: {n_belief} 件 / 新規 {kind_map.get('belief_new', 0)} 回・"
          f"統合更新 {kind_map.get('belief_update', 0)} 回")
    for r in conn.execute(
        "SELECT m.id, m.content, m.metadata FROM memories m "
        "WHERE m.type = 'belief' ORDER BY m.id DESC LIMIT 10"
    ).fetchall():
        print(f"  #{r['id']} {r['content'][:70]}")

    print("\n【意味づけ更新（再解釈）】")
    rows = conn.execute(
        "SELECT ts, target_id, old_value, new_value, reason FROM consolidation_log "
        "WHERE kind LIKE 'reinterpret%' ORDER BY ts DESC LIMIT 10"
    ).fetchall()
    if not rows:
        print("  （まだありません）")
    for r in rows:
        print(f"  [{r['ts'][:10]}] #{r['target_id']}: {r['old_value'][:40]} → {r['new_value'][:40]}")

    print("\n【計測（metrics）】")
    rows = conn.execute("SELECT key, value, ts FROM metrics ORDER BY ts ASC").fetchall()
    if not rows:
        print("  （まだ計測データがありません。/rate や蒸留採点で溜まります）")
    else:
        series: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            series[r["key"]].append(float(r["value"]))
        for key, values in series.items():
            recent = values[-5:]
            avg_all = sum(values) / len(values)
            avg_recent = sum(recent) / len(recent)
            arrow = "↑" if avg_recent > avg_all + 0.05 else ("↓" if avg_recent < avg_all - 0.05 else "→")
            print(f"  {key}: 全期間平均 {avg_all:.2f} / 直近5回 {avg_recent:.2f} {arrow} （n={len(values)}）")

    print("\n【監査線の未処理事項（要マスター確認）】")
    rows = conn.execute(
        "SELECT ts, kind, target_id, reason FROM consolidation_log "
        "WHERE kind = 'reinterpret_hold' ORDER BY ts DESC"
    ).fetchall()
    rows2 = conn.execute(
        "SELECT ts, param, reason FROM state_audit "
        "WHERE reason LIKE '%要マスター確認%' ORDER BY ts DESC"
    ).fetchall()
    if not rows and not rows2:
        print("  （なし）")
    for r in rows:
        print(f"  [{r['ts'][:10]}] 正典#{r['target_id']}への再解釈提案が保留: {r['reason'][:60]}")
    for r in rows2:
        print(f"  [{r['ts'][:10]}] {r['param']}: {r['reason'][:60]}")

    conn.close()


if __name__ == "__main__":
    main()
