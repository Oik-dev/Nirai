"""日記生成の永続状態。設計書v2 §4.5(2026-07-12改訂)。

`last_diary_at`（最後に日記を書いた時刻）と気分の軌跡(`mood_trajectory`)は電源断をまたいで
永続化する必要がある（旧設計はプロセス内メモリのみだったため、「夜に会話→電源断」運用では
朝起きても前回起動時の初期値に戻り、日記が一度も生成されない構造欠陥があった。DECISIONS
2026-07-12参照）。

`EmotionState`(core_v2/state/emotion.py)はLLM無しの決定論コアの一部としてI/Oを持たせない
方針のため、永続化はアプリ/Core境界のこのモジュールが担う（advisorレビュー2026-07-12）。
`quota_ledger.py`と同じ「JSON読み込み→都度アトミック保存」パターンを踏襲する。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIARY_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "diary_state.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_diary_state(
    path: Path | str = DEFAULT_DIARY_STATE_PATH,
) -> tuple[datetime, list[dict[str, float]]]:
    """(last_diary_at, mood_trajectory)を読み込む。ファイルが無ければ「今まさに起動した」
    扱いで初期化する（1度目の起動で過去分を遡って日記化しようとしないため）。
    """
    target = Path(path)
    if not target.exists():
        return _utc_now(), []
    data = json.loads(target.read_text(encoding="utf-8"))
    last_diary_at = datetime.fromisoformat(data["last_diary_at"])
    mood_trajectory = data.get("mood_trajectory", [])
    return last_diary_at, mood_trajectory


def save_diary_state(
    path: Path | str,
    *,
    last_diary_at: datetime,
    mood_trajectory: list[dict[str, float]],
) -> None:
    """アトミック保存（一時ファイル+os.replace。quota_ledger.py._saveと同じ理由）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_diary_at": last_diary_at.isoformat(),
        "mood_trajectory": mood_trajectory,
    }
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)
