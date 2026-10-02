"""episodic記憶生成の永続状態（旧: 日記生成の永続状態）。設計書 §4.5(2026-07-12改訂)。

`last_episodic_at`（最後にepisodic記憶を書いた時刻）と気分の軌跡(`mood_trajectory`)は電源断を
またいで永続化する必要がある（旧設計はプロセス内メモリのみだったため、「夜に会話→電源断」運用
では朝起きても前回起動時の初期値に戻り、episodic記憶が一度も生成されない構造欠陥があった。
DECISIONS 2026-07-12参照）。

`EmotionState`(core/state/emotion.py)はLLM無しの決定論コアの一部としてI/Oを持たせない
方針のため、永続化はアプリ/Core境界のこのモジュールが担う（advisorレビュー2026-07-12）。
`quota_ledger.py`と同じ「JSON読み込み→都度アトミック保存」パターンを踏襲する。

2026-07-23: type分類をepisodic/semanticへ統一する改訂に伴い、旧`diary_state.py`から改名。
旧ファイル(`data/diary_state.json`・キー`last_diary_at`)が残っている環境向けに、新ファイルが
無いときだけ旧ファイル・旧キーを読むフォールバックを持つ（一度でも保存されれば新形式に移行）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EPISODIC_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "episodic_state.json"
_LEGACY_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "diary_state.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_legacy(path: Path) -> tuple[datetime, list[dict[str, float]]] | None:
    """旧`diary_state.json`（キー`last_diary_at`）を読めれば返す。無ければNone。"""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    last_at = datetime.fromisoformat(data["last_diary_at"])
    mood_trajectory = data.get("mood_trajectory", [])
    return last_at, mood_trajectory


def load_episodic_state(
    path: Path | str = DEFAULT_EPISODIC_STATE_PATH,
) -> tuple[datetime, list[dict[str, float]]]:
    """(last_episodic_at, mood_trajectory)を読み込む。

    新ファイルが無ければ旧`diary_state.json`（存在すれば）を読み替えてフォールバックする
    （移行時に電源断耐性の継続性を失わないため）。どちらも無ければ「今まさに起動した」扱いで
    初期化する（1度目の起動で過去分を遡ってepisodic化しようとしないため）。
    """
    target = Path(path)
    if not target.exists():
        legacy = _load_legacy(_LEGACY_STATE_PATH)
        if legacy is not None:
            return legacy
        return _utc_now(), []
    data = json.loads(target.read_text(encoding="utf-8"))
    last_episodic_at = datetime.fromisoformat(data["last_episodic_at"])
    mood_trajectory = data.get("mood_trajectory", [])
    return last_episodic_at, mood_trajectory


def save_episodic_state(
    path: Path | str,
    *,
    last_episodic_at: datetime,
    mood_trajectory: list[dict[str, float]],
) -> None:
    """アトミック保存（一時ファイル+os.replace。quota_ledger.py._saveと同じ理由）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_episodic_at": last_episodic_at.isoformat(),
        "mood_trajectory": mood_trajectory,
    }
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)
