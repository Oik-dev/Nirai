"""セッション削除の副作用一括処理（宿題除去・記憶引用整理）のテスト。

2026-07-23: マスター要望「セッション削除＝発言1件削除を全発言分やった状態にしたい」
（テスト会話→セッション削除でテスト汚染を回避する運用）を受けて追加した
`purge_effects_for_session_rows` の検証。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.chore_box import ChoreBox
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.message_delete import _diary_target_day_label, purge_effects_for_session_rows
from serina.core.memory.protection import ChangeLog, GenerationStore
from serina.core.memory.store import MemoryStore


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store(tmp: Path) -> MemoryStore:
    return MemoryStore(tmp / "memory.db", embedder=_fake_embedder(), vector_dim=4)


def test_diary_target_day_label_shows_target_day_not_next_day() -> None:
    """2026-07-26是正(I-a): 日記のcreated_at（対象Serina日の終わり＝次Serina日の開始瞬間）
    をUTC文字列の単純切り出しで表示すると、boundary_hourがJSTオフセット(9)以上のとき
    翌日を指してしまう。JST変換して境界揃え(hour==7ちょうど)と判定できたら1日前を返す
    ことで、常に対象日そのものを表示することを検査する。

    2026-07-26是正C-1: 境界揃え判定はhour==SERINA_DAY_HOUR(既定7)まで絞っているため、
    表示層が正しく補正できるのはboundary_hourが既定値7のときのみ（`config/app_timing.toml`
    の`serina_day.boundary_hour`を変更した場合、表示層は追随しない既知の制約。
    metadataへの明示タグ付け方式へ移行するまでの暫定）。
    """
    # 2026-07-20分の日記のcreated_at = 対象日の終わり = 2026-07-21T07:00:00+09:00
    # = UTC 2026-07-20T22:00:00+00:00
    created_at = "2026-07-20T22:00:00+00:00"
    assert _diary_target_day_label(created_at) == "2026-07-20"


def test_diary_target_day_label_prefers_metadata_target_date() -> None:
    """2026-07-26恒久解: metadata.target_dateがあればcreated_atヒューリスティックより優先。"""
    # わざとヒューリスティックが別日を返すcreated_atを渡し、タグが勝つことを確認
    assert (
        _diary_target_day_label(
            "2026-07-21T12:00:00+09:00",
            {"target_date": "2026-07-20"},
        )
        == "2026-07-20"
    )


def test_diary_target_day_label_legacy_noon_default_not_misdetected_as_boundary() -> None:
    """2026-07-26是正(C-1): legacy投入記憶（旧日記）が時刻不明時のデフォルトとして
    多用する12:00:00ちょうど（実測: 28件中13件）を、境界揃えと誤検知して1日前へ
    ずらしてしまわないこと。"""
    assert _diary_target_day_label("2026-03-21T12:00:00+09:00") == "2026-03-21"


def test_purge_effects_for_session_rows_removes_chore_and_memory() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        box = ChoreBox(tmp / "chores.db")
        store = _fresh_store(tmp)
        change_log = ChangeLog(tmp / "c.jsonl")
        generation_store = GenerationStore(tmp / "g.jsonl")

        rows = [
            {"role": "user", "content": "テスト用の発言です", "ts": "2026-07-23T01:00:00+00:00"},
            {"role": "assistant", "content": "テスト用の返信です", "ts": "2026-07-23T01:00:05+00:00"},
        ]

        # このセッションの発言を含む未消化の蒸留下書き
        job_id = box.enqueue(
            "蒸留",
            lane="local",
            payload={
                "turns": [
                    {"speaker": "master", "text": "テスト用の発言です"},
                    {"speaker": "serina", "text": "テスト用の返信です"},
                ]
            },
        )

        # 完全一致の引用のみを持つ蒸留済み記憶（唯一の引用元が消えるので記憶ごと削除される想定）
        memory_id = store.add_memory(
            content="テスト用の発言についての要約",
            type="fact",
            protection_grade="B",
            source=None,
            metadata_obj={"source_quotes": ["テスト用の発言です"]},
        )

        outcome = purge_effects_for_session_rows(
            rows,
            chore_box=box,
            memory_store=store,
            change_log=change_log,
            generation_store=generation_store,
        )

        assert outcome.chore_jobs_removed == [job_id]
        assert box.pending() == []

        assert outcome.memories_deleted == [memory_id]
        assert store.get_memory_by_id(memory_id) is None


def test_purge_effects_for_session_rows_routes_memory_review_note_to_notes() -> None:
    """source_quotes 未保存の旧記憶への「要確認」注記は notes へ入り、
    diary_trace_notes（日記材料痕跡）へ誤混入しないこと（completion-review Important対応）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        box = ChoreBox(tmp / "chores.db")
        store = _fresh_store(tmp)
        change_log = ChangeLog(tmp / "c.jsonl")
        generation_store = GenerationStore(tmp / "g.jsonl")

        rows = [
            {"role": "user", "content": "テスト用の発言です", "ts": "2026-07-23T01:00:00+00:00"},
        ]

        # source_quotes 未保存（metadata無し）だが content に一致文字列を含む旧記憶
        memory_id = store.add_memory(
            content="テスト用の発言です、という話があった",
            type="fact",
            protection_grade="B",
            source=None,
        )

        outcome = purge_effects_for_session_rows(
            rows,
            chore_box=box,
            memory_store=store,
            change_log=change_log,
            generation_store=generation_store,
        )

        assert outcome.memories_deleted == []
        assert outcome.memories_quote_trimmed == []
        assert any(f"memory id={memory_id}" in n for n in outcome.notes)
        assert outcome.diary_trace_notes == []
        assert store.get_memory_by_id(memory_id) is not None


def test_purge_effects_for_session_rows_ignores_unrelated_chore() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        box = ChoreBox(tmp / "chores.db")
        store = _fresh_store(tmp)
        change_log = ChangeLog(tmp / "c.jsonl")
        generation_store = GenerationStore(tmp / "g.jsonl")

        unrelated_job_id = box.enqueue(
            "蒸留",
            lane="local",
            payload={"turns": [{"speaker": "master", "text": "別セッションの発言"}]},
        )

        rows = [
            {"role": "user", "content": "今回のセッションの発言", "ts": "2026-07-23T01:00:00+00:00"},
        ]

        outcome = purge_effects_for_session_rows(
            rows,
            chore_box=box,
            memory_store=store,
            change_log=change_log,
            generation_store=generation_store,
        )

        assert outcome.chore_jobs_removed == []
        assert [job.id for job in box.pending()] == [unrelated_job_id]
