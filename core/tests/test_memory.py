from pathlib import Path
import json
import sqlite3
import threading

import pytest

from core.memory.private import PrivateMemoryService
from core.memory.retriever import WorldMemoryRetriever
from core.memory.world import WorldMemoryService


def make_resident(root: Path, name: str = "Lapan") -> None:
    resident_dir = root / "residents" / name
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "config.toml").write_text('brain = "codex"\n', encoding="utf-8")


def test_memory_job_tables_have_pending_lookup_indexes(tmp_path: Path) -> None:
    make_resident(tmp_path)
    world = WorldMemoryService(tmp_path)
    private = PrivateMemoryService(tmp_path)
    private.private_db_path("Lapan")

    with sqlite3.connect(world.db_path) as connection:
        world_indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list('structured_jobs')").fetchall()
        }
    with sqlite3.connect(private.private_db_path("Lapan")) as connection:
        private_indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list('embedding_jobs')").fetchall()
        }

    assert "structured_jobs_status_raw" in world_indexes
    assert "embedding_jobs_status_retry" in private_indexes


def test_private_memory_restart_skips_full_compat_rewrite_when_derived_state_is_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_resident(tmp_path)
    first = PrivateMemoryService(tmp_path)
    first.append_whisper(
        "Lapan",
        session_id="S-PRIVATE-RESTART",
        sender="master",
        recipient="Lapan",
        text="PRIVATE-RESTART-SENTINEL",
        entry_id="CE-PRIVATE-RESTART",
    )

    second = PrivateMemoryService(tmp_path)

    def fail_rewrite(_private_dir: Path) -> None:
        raise AssertionError("normal restart must not rewrite the full compatibility JSONL")

    monkeypatch.setattr(second, "_rewrite_legacy_jsonl", fail_rewrite)
    context = second.context_for_brain("Lapan", "S-PRIVATE-RESTART")
    assert "PRIVATE-RESTART-SENTINEL" in context["private_context"]
    assert [entry["entry_id"] for entry in context["recent_whispers"]] == [
        "CE-PRIVATE-RESTART"
    ]


def test_private_memory_missing_derived_signature_rebuilds_compat_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_resident(tmp_path)
    first = PrivateMemoryService(tmp_path)
    first.append_whisper(
        "Lapan",
        session_id="S-PRIVATE-MIGRATION",
        sender="master",
        recipient="Lapan",
        text="PRIVATE-MIGRATION-SENTINEL",
        entry_id="CE-PRIVATE-MIGRATION",
    )
    db_path = tmp_path / "residents" / "Lapan" / "private" / "private_memory.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM migration_meta WHERE key='derived_state_signature'"
        )
        connection.commit()

    second = PrivateMemoryService(tmp_path)
    original_rewrite = second._rewrite_legacy_jsonl
    calls = 0

    def counted_rewrite(private_dir: Path) -> None:
        nonlocal calls
        calls += 1
        original_rewrite(private_dir)

    monkeypatch.setattr(second, "_rewrite_legacy_jsonl", counted_rewrite)
    assert second.recent_whispers("Lapan")[-1]["entry_id"] == "CE-PRIVATE-MIGRATION"
    assert calls == 1

    third = PrivateMemoryService(tmp_path)

    def fail_rewrite(_private_dir: Path) -> None:
        raise AssertionError("migration rebuild should persist the derived-state marker")

    monkeypatch.setattr(third, "_rewrite_legacy_jsonl", fail_rewrite)
    assert third.recent_whispers("Lapan")[-1]["entry_id"] == "CE-PRIVATE-MIGRATION"


def test_private_memory_keeps_old_whisper_without_date_expiry(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-OLD",
        sender="master",
        recipient="Lapan",
        text="30日前の秘密",
        request_id="REQ-OLD",
        ts="2026-07-29T12:00:00+09:00",
    )

    context = memory.context_for_brain("Lapan", "S-NEW")

    assert any(entry["text"] == "30日前の秘密" for entry in context["recent_whispers"])
    assert "30日前の秘密" in context["private_context"]


def test_private_whisper_continuation_crosses_public_chat_session_boundaries(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-ONE",
        sender="master",
        recipient="Lapan",
        text="最初の秘密",
        entry_id="CE-PRIVATE-1",
    )
    memory.append_whisper(
        "Lapan",
        session_id="S-TWO",
        sender="Lapan",
        recipient="master",
        text="別の公開Chat Sessionに移った後の返答",
        entry_id="CE-PRIVATE-2",
    )

    delta = memory.whispers_after("Lapan", "CE-PRIVATE-1")

    assert [entry["entry_id"] for entry in delta] == ["CE-PRIVATE-2"]
    assert delta[0]["session"] == "S-TWO"


def test_world_memory_records_only_public_entries_once(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    public = {
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "公開の話",
        "session": "S-20260828-001",
        "request_id": "REQ-PUBLIC",
    }
    resident_chat = {
        "ts": "2026-08-28T12:00:30+09:00",
        "kind": "resident_chat",
        "from": "Lapan",
        "to": "Kina",
        "text": "住人同士の公開会話",
        "session": "S-20260828-001",
    }
    holo_say = {
        "ts": "2026-08-28T12:00:45+09:00",
        "kind": "holo_say",
        "from": "Holo",
        "text": "ホロの公開会話",
        "session": "S-20260828-001",
    }
    whisper = {
        "ts": "2026-08-28T12:01:00+09:00",
        "kind": "whisper",
        "from": "master",
        "to": "Lapan",
        "text": "秘密の話",
        "session": "S-20260828-001",
        "request_id": "REQ-PRIVATE",
    }

    memory.record_public_entry(public)
    memory.record_public_entry(public)
    memory.record_public_entry(resident_chat)
    memory.record_public_entry(holo_say)
    memory.record_public_entry(whisper)

    paths = memory.episodes_for_session("S-20260828-001")
    assert len(paths) == 1
    text = paths[0].read_text(encoding="utf-8")
    assert text.count("公開の話") == 1
    assert text.count("住人同士の公開会話") == 1
    assert text.count("ホロの公開会話") == 1
    assert "秘密の話" not in text


def test_world_memory_raw_source_is_lossless_even_when_compat_episode_is_truncated(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    long_text = "深海の記録:" + ("あいうえお" * 120)
    entry = {
        "entry_id": "CE-LONG-RAW",
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": long_text,
        "session": "S-LONG-RAW",
        "request_id": "REQ-LONG-RAW",
    }

    memory.record_public_entry(entry)

    raw = memory.raw_entries_for_session("S-LONG-RAW")
    assert raw == [entry]
    episode = memory.episodes_for_session("S-LONG-RAW")[0].read_text(encoding="utf-8")
    assert long_text not in episode
    assert long_text[:240] in episode
    assert memory.pending_raw_entries() == [entry]


def test_world_memory_compat_episode_appends_without_reading_existing_file(tmp_path: Path, monkeypatch) -> None:
    memory = WorldMemoryService(tmp_path)
    first = {
        "entry_id": "CE-APPEND-1",
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "最初の公開会話",
        "session": "S-APPEND-ONLY",
    }
    second = {
        "entry_id": "CE-APPEND-2",
        "ts": "2026-08-28T12:01:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "次の公開会話",
        "session": "S-APPEND-ONLY",
    }
    memory.record_public_entry(first)
    episode = memory.episodes_for_session("S-APPEND-ONLY")[0]
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == episode:
            raise AssertionError("compat Episode append must not read/rewrite the existing file")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    memory.record_public_entry(second)

    with episode.open("r", encoding="utf-8") as handle:
        text = handle.read()
    assert "最初の公開会話" in text
    assert "次の公開会話" in text


def test_world_memory_raw_source_dedupes_stable_entry_id_and_forget_cascades_job(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    entry = {
        "entry_id": "CE-RAW-DEDUP",
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "resident_chat",
        "from": "Lapan",
        "to": "Kina",
        "text": "全文で残す公開会話",
        "session": "S-RAW-DEDUP",
    }

    memory.record_public_entry(entry)
    memory.record_public_entry(entry)

    assert memory.raw_entries_for_session("S-RAW-DEDUP") == [entry]
    assert memory.pending_raw_entries() == [entry]
    assert memory.forget_session("S-RAW-DEDUP") == 1
    assert memory.raw_entries_for_session("S-RAW-DEDUP") == []
    assert memory.pending_raw_entries() == []


def test_world_memory_dedupes_same_entry_but_keeps_same_text_as_separate_entries(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    first = {
        "entry_id": "CE-FIRST",
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "resident_chat",
        "from": "Lapan",
        "to": "Kina",
        "text": "了解です",
        "session": "S-REPEAT",
    }
    second = {
        "entry_id": "CE-SECOND",
        "ts": "2026-08-28T12:05:00+09:00",
        "kind": "resident_chat",
        "from": "Lapan",
        "to": "Kina",
        "text": "了解です",
        "session": "S-REPEAT",
    }

    memory.record_public_entry(first)
    memory.record_public_entry(first)
    memory.record_public_entry(second)

    episode = memory.episodes_for_session("S-REPEAT")[0].read_text(encoding="utf-8")
    assert episode.count("了解です") == 2
    assert episode.count("<!-- entry:") == 2


def test_world_memory_forget_removes_episode_only(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "resident_say",
        "from": "Lapan",
        "text": "覚えておく公開会話",
        "session": "S-20260828-001",
        "request_id": "REQ-1",
    })

    assert memory.forget_session("S-20260828-001") == 1
    assert memory.episodes_for_session("S-20260828-001") == []


def test_world_memory_retriever_does_not_rescan_episode_directory_on_every_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episodes = tmp_path / "world_memory" / "episodes"
    episodes.mkdir(parents=True)
    (episodes / "S-LEGACY-E001.md").write_text(
        "session_id: S-LEGACY\n"
        "episode_id: S-LEGACY-E001\n"
        "- 2025-01-01T00:00:00+09:00 Master: 古い灯台に銀色の鍵を隠した\n",
        encoding="utf-8",
    )
    retriever = WorldMemoryRetriever(tmp_path)
    original_sync = retriever._sync_sources
    calls = 0

    def counted_sync(connection, *, tokenizer: str, force: bool = False) -> int:
        nonlocal calls
        calls += 1
        return original_sync(connection, tokenizer=tokenizer, force=force)

    monkeypatch.setattr(retriever, "_sync_sources", counted_sync)
    assert retriever.search("銀色の鍵")
    assert calls == 1
    assert retriever.search("古い灯台")
    assert calls == 1


def test_world_memory_retriever_finds_relevant_japanese_episode_with_fts5(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "前に三人で海の奥へ行こうと話したね",
        "session": "S-SEA",
        "request_id": "REQ-SEA",
    })
    memory.record_public_entry({
        "ts": "2026-08-29T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "今日の夕食はパンにしよう",
        "session": "S-BREAD",
        "request_id": "REQ-BREAD",
    })
    retriever = WorldMemoryRetriever(tmp_path)

    hits = retriever.search("海の奥の話を覚えてる？", top_k=3)

    assert [hit.session_id for hit in hits] == ["S-SEA"]
    assert "海の奥へ行こう" in hits[0].excerpt
    assert retriever.db_path.is_file()


def test_world_memory_retriever_native_trigram_supports_two_char_japanese_and_mixed_terms(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "entry_id": "CE-TRIGRAM",
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "昨日花火を見てNirai42の話をした",
        "session": "S-TRIGRAM",
        "request_id": "REQ-TRIGRAM",
    })
    retriever = WorldMemoryRetriever(tmp_path)

    assert retriever.search("花火")[0].session_id == "S-TRIGRAM"
    assert retriever.search("昨日花火")[0].session_id == "S-TRIGRAM"
    assert retriever.search("花火 Nirai42")[0].session_id == "S-TRIGRAM"
    assert retriever.search("花") == []
    with sqlite3.connect(retriever.db_path) as connection:
        tokenizer = connection.execute(
            "SELECT value FROM retriever_meta WHERE key='tokenizer'"
        ).fetchone()
    assert tokenizer == ("trigram",)


def test_world_memory_retriever_unicode61_fallback_supports_japanese_partial_search(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "entry_id": "CE-FIREWORKS",
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "昨日花火を見た",
        "session": "S-FIREWORKS",
        "request_id": "REQ-FIREWORKS",
    })
    retriever = WorldMemoryRetriever(tmp_path)
    retriever.index_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(retriever.db_path) as connection:
        connection.execute(
            "CREATE TABLE retriever_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE episode_source (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                episode_id TEXT NOT NULL,
                session_id TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE episodes_fts USING fts5(
                episode_id UNINDEXED,
                session_id UNINDEXED,
                path UNINDEXED,
                content UNINDEXED,
                search_text,
                tokenize='unicode61'
            )
            """
        )
        connection.execute(
            "INSERT INTO retriever_meta(key, value) VALUES('tokenizer', 'unicode61')"
        )
        connection.execute(
            "INSERT INTO retriever_meta(key, value) VALUES('index_format', 'ngram-v3')"
        )
        connection.commit()

    hits = retriever.search("花火")

    assert len(hits) == 1
    assert hits[0].session_id == "S-FIREWORKS"
    assert "昨日花火を見た" in hits[0].excerpt


def test_world_memory_retriever_rebuilds_after_derived_index_is_deleted(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "青い貝殻を見つけた",
        "session": "S-SHELL",
        "request_id": "REQ-SHELL",
    })
    retriever = WorldMemoryRetriever(tmp_path)
    assert retriever.search("青い貝殻")
    retriever.db_path.unlink()

    hits = retriever.search("青い貝殻")

    assert len(hits) == 1
    assert hits[0].session_id == "S-SHELL"
    assert retriever.db_path.is_file()


def test_world_memory_retriever_never_indexes_private_memory(tmp_path: Path) -> None:
    make_resident(tmp_path)
    private = PrivateMemoryService(tmp_path)
    private.append_whisper(
        "Lapan",
        session_id="S-PRIVATE",
        sender="master",
        recipient="Lapan",
        text="PRIVATE-DRAGON-SENTINEL 秘密の竜は北にいる",
        request_id="REQ-PRIVATE",
    )
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "公開では白い砂の話だけをした",
        "session": "S-PUBLIC",
        "request_id": "REQ-PUBLIC",
    })
    retriever = WorldMemoryRetriever(tmp_path)

    assert retriever.search("秘密の竜は北にいる") == []
    assert retriever.search("PRIVATE-DRAGON-SENTINEL") == []


def test_world_memory_retriever_excludes_current_session_and_syncs_forget(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "珊瑚の洞窟で光る魚を見た",
        "session": "S-CORAL",
        "request_id": "REQ-CORAL",
    })
    retriever = WorldMemoryRetriever(tmp_path)

    assert retriever.search("珊瑚の洞窟")
    assert retriever.search("珊瑚の洞窟", exclude_session_id="S-CORAL") == []

    assert memory.forget_session("S-CORAL") == 1
    assert retriever.search("珊瑚の洞窟") == []


def test_world_memory_retriever_keeps_old_memory_from_same_long_session(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    entries: list[dict[str, str]] = []
    for index in range(25):
        entry = {
            "entry_id": f"CE-LONG-{index:02d}",
            "ts": f"2026-08-28T12:{index:02d}:00+09:00",
            "kind": "say",
            "from": "master",
            "text": (
                "青い真珠を洞窟に隠した大切な約束"
                if index == 0
                else f"日常の雑談その{index:02d}"
            ),
            "session": "S-LONG",
            "request_id": f"REQ-LONG-{index:02d}",
        }
        entries.append(entry)
        memory.record_public_entry(entry)

    recent_markers = {memory.entry_marker(entry) for entry in entries[-20:]}
    hits = WorldMemoryRetriever(tmp_path).search(
        "青い真珠を洞窟に隠した約束",
        exclude_entry_markers=recent_markers,
    )

    assert len(hits) == 1
    assert hits[0].session_id == "S-LONG"
    assert "青い真珠を洞窟に隠した大切な約束" in hits[0].excerpt


def test_world_memory_retriever_excerpt_centers_late_matching_entry_and_ignores_excluded_match(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    entries: list[dict[str, str]] = []
    for index in range(22):
        entry = {
            "entry_id": f"CE-EXCERPT-{index:02d}",
            "ts": f"2026-08-28T13:{index:02d}:00+09:00",
            "kind": "say",
            "from": "master",
            "text": f"長い前置きの公開会話{index:02d} " + ("砂浜の雑談" * 8),
            "session": "S-EXCERPT",
            "request_id": f"REQ-EXCERPT-{index:02d}",
        }
        entries.append(entry)
        memory.record_public_entry(entry)
    target = {
        "entry_id": "CE-EXCERPT-TARGET",
        "ts": "2026-08-28T13:30:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "深海鐘を見つけた重要な記憶",
        "session": "S-EXCERPT",
        "request_id": "REQ-EXCERPT-TARGET",
    }
    memory.record_public_entry(target)
    retriever = WorldMemoryRetriever(tmp_path)

    hits = retriever.search("深海鐘")
    assert len(hits) == 1
    assert "深海鐘を見つけた重要な記憶" in hits[0].excerpt
    assert len(hits[0].excerpt) <= 1200

    excluded = {memory.entry_marker(target)}
    assert retriever.search("深海鐘", exclude_entry_markers=excluded) == []


def test_world_memory_retriever_returns_zero_for_unrelated_query(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "水面の光が綺麗だった",
        "session": "S-LIGHT",
        "request_id": "REQ-LIGHT",
    })

    assert WorldMemoryRetriever(tmp_path).search("量子コンピュータの冷却方式") == []


def test_world_memory_retriever_skips_invalid_utf8_episode_files(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "前に三人で海の奥へ行こうと話したね",
        "session": "S-SEA",
        "request_id": "REQ-SEA",
    })
    retriever = WorldMemoryRetriever(tmp_path)
    (retriever.episodes_root / "S-BAD-E001.md").write_bytes(b"\xff\xfe not utf-8")

    hits = retriever.search("海の奥の話を覚えてる？", top_k=3)
    assert [hit.session_id for hit in hits] == ["S-SEA"]


def test_private_memory_imports_legacy_jsonl_once_then_reads_sqlite(tmp_path: Path, monkeypatch) -> None:
    make_resident(tmp_path)
    private_dir = tmp_path / "residents" / "Lapan" / "private"
    private_dir.mkdir(parents=True)
    legacy = private_dir / "whispers.jsonl"
    legacy.write_text(
        '{"ts":"2025-01-01T10:00:00+09:00","session":"S-LEGACY","from":"master","to":"Lapan","text":"LEGACY-PRIVATE-SENTINEL","entry_id":"CE-LEGACY-PRIVATE"}\n',
        encoding="utf-8",
    )
    memory = PrivateMemoryService(tmp_path)

    assert [item["entry_id"] for item in memory.recent_whispers("Lapan", 10)] == ["CE-LEGACY-PRIVATE"]
    assert memory.raw_count("Lapan") == 1
    assert (private_dir / "private_memory.sqlite3").is_file()

    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == legacy:
            raise AssertionError("normal Private reads must not rescan whispers.jsonl")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path, *args, **kwargs):
        if path == legacy:
            raise AssertionError("normal Private reads must not rescan whispers.jsonl")
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    assert memory.recent_whispers("Lapan", 1)[0]["text"] == "LEGACY-PRIVATE-SENTINEL"
    assert memory.whispers_after("Lapan", "CE-LEGACY-PRIVATE") == []


def test_private_memory_legacy_jsonl_skips_invalid_utf8_lines(tmp_path: Path) -> None:
    make_resident(tmp_path)
    private_dir = tmp_path / "residents" / "Lapan" / "private"
    private_dir.mkdir(parents=True)
    valid = (
        '{"ts":"2025-01-01T10:00:00+09:00","session":"S-LEGACY",'
        '"from":"master","to":"Lapan","text":"OK-LINE","entry_id":"CE-OK"}\n'
    )
    (private_dir / "whispers.jsonl").write_bytes(valid.encode("utf-8") + b"\xff\xfe\n")
    memory = PrivateMemoryService(tmp_path)
    assert [item["entry_id"] for item in memory.recent_whispers("Lapan", 10)] == ["CE-OK"]


def test_private_memory_search_is_physically_scoped_per_resident(tmp_path: Path) -> None:
    make_resident(tmp_path, "Lapan")
    make_resident(tmp_path, "Kina")
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-LAPAN",
        sender="master",
        recipient="Lapan",
        text="Lapanだけの保守コードはZX-LAPAN-77",
        entry_id="CE-LAPAN-SECRET",
    )
    memory.append_whisper(
        "Kina",
        session_id="S-KINA",
        sender="master",
        recipient="Kina",
        text="Kinaだけの保守コードはZX-KINA-99",
        entry_id="CE-KINA-SECRET",
    )

    lapan_hits = memory.search("Lapan", "ZX-LAPAN-77は何だっけ？")
    assert [hit.entry_id for hit in lapan_hits] == ["CE-LAPAN-SECRET"]
    assert memory.search("Lapan", "ZX-KINA-99は何だっけ？") == []
    assert memory.search("Kina", "ZX-LAPAN-77は何だっけ？") == []


def test_private_memory_search_abstains_on_near_miss(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-SEA",
        sender="master",
        recipient="Lapan",
        text="疲れた時は海辺にいると落ち着く",
        entry_id="CE-PRIVATE-SEA",
    )

    assert memory.search("Lapan", "海辺に行った日の駐車料金はいくら？") == []


def test_private_memory_forget_entry_removes_sqlite_fts_and_compat_jsonl(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-FORGET",
        sender="master",
        recipient="Lapan",
        text="PRIVATE-FORGET-991を忘れて",
        entry_id="CE-PRIVATE-FORGET",
    )
    assert memory.search("Lapan", "PRIVATE-FORGET-991")

    assert memory.forget_entry("Lapan", "CE-PRIVATE-FORGET") is True
    assert memory.search("Lapan", "PRIVATE-FORGET-991") == []
    assert memory.raw_count("Lapan") == 0
    assert memory.recent_whispers("Lapan", 10) == []
    assert "PRIVATE-FORGET-991" not in (
        tmp_path / "residents" / "Lapan" / "private" / "whispers.jsonl"
    ).read_text(encoding="utf-8")


def test_private_memory_forget_serializes_append_that_checked_tombstone_first(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    private_dir = memory._ensure_store("Lapan")
    entry_id = "CE-PRIVATE-FORGET-RACE-APPEND-FIRST"
    entry = {
        "entry_id": entry_id,
        "ts": "2026-09-08T00:00:00+09:00",
        "session": "S-FORGET-RACE",
        "from": "master",
        "to": "Lapan",
        "text": "append first race",
    }
    original_connect = memory._connect
    tombstone_checked = threading.Event()
    release_append = threading.Event()
    append_thread_id: list[int] = []
    append_result: list[bool] = []
    forget_result: list[bool] = []
    errors: list[BaseException] = []

    class GateAfterTombstoneCheck:
        def __init__(self, inner) -> None:
            self.inner = inner

        def execute(self, sql, parameters=()):
            cursor = self.inner.execute(sql, parameters)
            if "SELECT 1 FROM forgotten_entries WHERE entry_id" in sql:
                tombstone_checked.set()
                if not release_append.wait(timeout=5):
                    raise AssertionError("append race gate timed out")
            return cursor

        def __getattr__(self, name):
            return getattr(self.inner, name)

    def gated_connect(path: Path):
        inner = original_connect(path)
        if append_thread_id and threading.get_ident() == append_thread_id[0]:
            return GateAfterTombstoneCheck(inner)
        return inner

    memory._connect = gated_connect  # type: ignore[method-assign]

    def append_worker() -> None:
        append_thread_id.append(threading.get_ident())
        try:
            append_result.append(memory._insert_raw_entry(private_dir, entry))
        except BaseException as exc:  # pragma: no cover - assertion reports below
            errors.append(exc)

    def forget_worker() -> None:
        try:
            forget_result.append(memory.forget_entry("Lapan", entry_id))
        except BaseException as exc:  # pragma: no cover - assertion reports below
            errors.append(exc)

    append_thread = threading.Thread(target=append_worker)
    append_thread.start()
    assert tombstone_checked.wait(timeout=5)
    forget_thread = threading.Thread(target=forget_worker)
    forget_thread.start()

    # append owns BEGIN IMMEDIATE while paused after the tombstone read, so
    # Forget cannot overtake it and commit a tombstone before its Raw write.
    assert forget_thread.join(timeout=0.1) is None
    assert forget_thread.is_alive()
    release_append.set()
    append_thread.join(timeout=5)
    forget_thread.join(timeout=5)

    assert errors == []
    assert append_result == [True]
    assert forget_result == [True]
    with original_connect(private_dir) as connection:
        assert connection.execute(
            "SELECT 1 FROM forgotten_entries WHERE entry_id=?",
            (entry_id,),
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM raw_entries WHERE entry_id=?",
            (entry_id,),
        ).fetchone() is None


def test_private_memory_forget_serializes_append_that_arrives_after_forget_lock(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    private_dir = memory._ensure_store("Lapan")
    entry_id = "CE-PRIVATE-FORGET-RACE-FORGET-FIRST"
    entry = {
        "entry_id": entry_id,
        "ts": "2026-09-08T00:00:00+09:00",
        "session": "S-FORGET-RACE",
        "from": "master",
        "to": "Lapan",
        "text": "forget first race",
    }
    original_connect = memory._connect
    raw_lookup_done = threading.Event()
    release_forget = threading.Event()
    forget_thread_id: list[int] = []
    append_result: list[bool] = []
    forget_result: list[bool] = []
    errors: list[BaseException] = []

    class GateAfterRawLookup:
        def __init__(self, inner) -> None:
            self.inner = inner

        def execute(self, sql, parameters=()):
            cursor = self.inner.execute(sql, parameters)
            if "SELECT raw_id FROM raw_entries WHERE entry_id" in sql:
                raw_lookup_done.set()
                if not release_forget.wait(timeout=5):
                    raise AssertionError("forget race gate timed out")
            return cursor

        def __getattr__(self, name):
            return getattr(self.inner, name)

    def gated_connect(path: Path):
        inner = original_connect(path)
        if forget_thread_id and threading.get_ident() == forget_thread_id[0]:
            return GateAfterRawLookup(inner)
        return inner

    memory._connect = gated_connect  # type: ignore[method-assign]

    def forget_worker() -> None:
        forget_thread_id.append(threading.get_ident())
        try:
            forget_result.append(memory.forget_entry("Lapan", entry_id))
        except BaseException as exc:  # pragma: no cover - assertion reports below
            errors.append(exc)

    def append_worker() -> None:
        try:
            append_result.append(memory._insert_raw_entry(private_dir, entry))
        except BaseException as exc:  # pragma: no cover - assertion reports below
            errors.append(exc)

    forget_thread = threading.Thread(target=forget_worker)
    forget_thread.start()
    assert raw_lookup_done.wait(timeout=5)
    append_thread = threading.Thread(target=append_worker)
    append_thread.start()

    # Forget already owns BEGIN IMMEDIATE. The late append must wait, then see
    # the committed tombstone instead of recreating Raw.
    append_thread.join(timeout=0.1)
    assert append_thread.is_alive()
    release_forget.set()
    forget_thread.join(timeout=5)
    append_thread.join(timeout=5)

    assert errors == []
    assert forget_result == [False]
    assert append_result == [False]
    with original_connect(private_dir) as connection:
        assert connection.execute(
            "SELECT 1 FROM forgotten_entries WHERE entry_id=?",
            (entry_id,),
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM raw_entries WHERE entry_id=?",
            (entry_id,),
        ).fetchone() is None


def test_private_memory_forget_derived_refresh_failure_repairs_before_next_brain_context(
    tmp_path: Path,
) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-FORGET-DERIVED-FAIL",
        sender="master",
        recipient="Lapan",
        text="PRIVATE-DERIVED-FAIL-991",
        entry_id="CE-PRIVATE-DERIVED-FAIL",
    )
    original_rewrite = memory._rewrite_legacy_jsonl
    calls = 0

    def fail_once(private_dir: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("simulated derived rewrite failure")
        original_rewrite(private_dir)

    memory._rewrite_legacy_jsonl = fail_once  # type: ignore[method-assign]
    try:
        memory.forget_entry("Lapan", "CE-PRIVATE-DERIVED-FAIL")
    except Exception as exc:
        assert "derived private files could not be refreshed" in str(exc)
    else:  # pragma: no cover - failure injection must surface the partial repair state
        raise AssertionError("forget should report the derived refresh failure")

    # Authoritative Forget committed despite the derived-file failure.
    assert memory.raw_count("Lapan") == 0
    assert memory.search("Lapan", "PRIVATE-DERIVED-FAIL-991") == []

    # The failed repair invalidates the ready cache. Before Brain context can be
    # exposed again, derived files are rebuilt from the tombstoned SQLite source.
    context = memory.context_for_brain("Lapan", "S-FORGET-DERIVED-FAIL")
    assert "PRIVATE-DERIVED-FAIL-991" not in context["private_context"]
    assert context["recent_whispers"] == []


def test_private_memory_forget_tombstone_repairs_stale_derived_files_after_restart(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    entry = memory.append_whisper(
        "Lapan",
        session_id="S-FORGET-CRASH",
        sender="master",
        recipient="Lapan",
        text="PRIVATE-CRASH-FORGET-772",
        entry_id="CE-PRIVATE-CRASH-FORGET",
    )
    assert memory.forget_entry("Lapan", "CE-PRIVATE-CRASH-FORGET") is True

    private_dir = tmp_path / "residents" / "Lapan" / "private"
    # Simulate a process death after the authoritative SQLite transaction
    # committed but before compatibility/context files were refreshed.
    (private_dir / "whispers.jsonl").write_text(
        json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (private_dir / "context.md").write_text(
        "# Private Context\n\nPRIVATE-CRASH-FORGET-772\n",
        encoding="utf-8",
    )

    restarted = PrivateMemoryService(tmp_path)
    assert restarted.recent_whispers("Lapan", 10) == []
    context = restarted.context_for_brain("Lapan", "S-FORGET-CRASH")
    assert "PRIVATE-CRASH-FORGET-772" not in context["private_context"]
    assert "PRIVATE-CRASH-FORGET-772" not in (private_dir / "whispers.jsonl").read_text(encoding="utf-8")
    assert restarted.raw_count("Lapan") == 0
    assert restarted.forget_entry("Lapan", "CE-PRIVATE-CRASH-FORGET") is False
