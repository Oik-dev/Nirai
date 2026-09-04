from pathlib import Path
import sqlite3

from core.memory.private import PrivateMemoryService
from core.memory.retriever import WorldMemoryRetriever
from core.memory.world import WorldMemoryService


def make_resident(root: Path, name: str = "Lapan") -> None:
    resident_dir = root / "residents" / name
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "config.toml").write_text('brain = "codex"\n', encoding="utf-8")


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
    memory.record_public_entry(whisper)

    paths = memory.episodes_for_session("S-20260828-001")
    assert len(paths) == 1
    text = paths[0].read_text(encoding="utf-8")
    assert text.count("公開の話") == 1
    assert text.count("住人同士の公開会話") == 1
    assert "秘密の話" not in text


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
