"""life/ 一方向生成（A10）のスモークテスト。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore
from tools.export_life import export_life


def _fake_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(call_fn=lambda _m, _t: [1.0, 0.0, 0.0, 0.0])


def test_export_life_from_temp_db() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "test_memory.db"
        life_dir = tmp / "life"
        summaries_path = tmp / "summaries" / "blocks.json"
        summaries_path.parent.mkdir(parents=True)
        summaries_path.write_text(
            json.dumps(
                {
                    "prefs_summary": {"content": "甘いものが好き"},
                    "relation_summary": {"content": "最近は穏やか"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        store = MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)
        mem_id = store.add_memory("散歩が好き", type="fact")
        store.facts.add_fact(
            subject="master",
            predicate="likes",
            object="walk",
            statement="マスターは散歩が好き",
            episode_ids=[mem_id],
        )

        written = export_life(
            db_path, life_dir,
            summaries_path=summaries_path,
            weekly_log_path=tmp / "weekly.json",  # 本物の data/ を汚さない
        )
        assert len(written) >= 4
        index_text = (life_dir / "index.md").read_text(encoding="utf-8")
        assert "記憶件数: 1" in index_text
        facts_text = (life_dir / "facts.md").read_text(encoding="utf-8")
        assert "散歩が好き" in facts_text
        summaries_text = (life_dir / "summaries.md").read_text(encoding="utf-8")
        assert "甘いものが好き" in summaries_text


def test_export_life_fingerprint_ignores_generated_timestamp() -> None:
    """生成時刻だけが違う再 export では内容指紋が変わらない（可視成長の常時満点化防止）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "test_memory.db"
        life_dir = tmp / "life"
        log = tmp / "weekly.json"

        store = MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)
        store.add_memory("散歩が好き", type="fact")

        export_life(db_path, life_dir, weekly_log_path=log)
        fp1 = json.loads(log.read_text(encoding="utf-8"))["last_fingerprint"]

        export_life(db_path, life_dir, weekly_log_path=log)
        payload = json.loads(log.read_text(encoding="utf-8"))
        assert payload["last_fingerprint"] == fp1, "index.md の生成時刻で指紋が汚染されている"
        assert payload["weeks"][0]["exports"] == 2


def test_record_life_weekly_new_week_without_change_is_not_nonempty() -> None:
    """週初回でも内容指紋が前回と同じなら「非空」と数えない（可視成長の甘さ対策）。"""
    from datetime import datetime, timezone

    from tools.export_life import record_life_weekly

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        md = tmp / "diary.md"
        md.write_text("同じ内容", encoding="utf-8")
        log = tmp / "weekly.json"

        week1 = datetime(2026, 7, 6, tzinfo=timezone.utc)
        week2 = datetime(2026, 7, 13, tzinfo=timezone.utc)
        y2, w2, _ = week2.isocalendar()
        week2_key = f"{y2}-W{w2:02d}"

        # 初回（過去指紋なし）は変化ありとして非空
        payload = record_life_weekly([md], log_path=log, now=week1)
        assert payload["weeks"][0]["nonempty"] is True

        # 翌週、内容が一字も変わっていない → 非空にしない
        payload = record_life_weekly([md], log_path=log, now=week2)
        week2_entry = next(w for w in payload["weeks"] if w["week"] == week2_key)
        assert week2_entry["nonempty"] is False

        # 同じ週の後続 export で内容が変わった → 非空へ昇格
        md.write_text("変わった内容", encoding="utf-8")
        payload = record_life_weekly([md], log_path=log, now=week2)
        week2_entry = next(w for w in payload["weeks"] if w["week"] == week2_key)
        assert week2_entry["nonempty"] is True


def test_record_life_weekly_survives_corrupt_log() -> None:
    """週次ログが壊れていても例外にせず、新しいログとして書き直す。"""
    from tools.export_life import record_life_weekly

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        md = tmp / "diary.md"
        md.write_text("中身", encoding="utf-8")
        log = tmp / "weekly.json"
        log.write_text("{{{壊れたJSON", encoding="utf-8")

        payload = record_life_weekly([md], log_path=log)
        assert payload["weeks"], "壊れたログは作り直して継続する"
        assert json.loads(log.read_text(encoding="utf-8"))["weeks"]
