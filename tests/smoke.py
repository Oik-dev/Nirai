"""Memory層スモークテスト — 人間が読める日本語レポートを標準出力"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.embedder import OllamaEmbedder
from serina.memory.config import SearchConfig
from serina.memory.db import init_db, list_tables
from serina.memory.store import MemoryStore


def _section(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def test_db_init() -> None:
    _section("Step1: DB初期化")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_memory.db"
        init_db(db_path)
        tables = set(list_tables(db_path))
        expected = {"profile", "memories", "memory_vec", "history"}
        ok = expected.issubset(tables)
        print(f"DB: {db_path}")
        print(f"テーブル: {sorted(tables)}")
        print("結果: テーブル4つ作成完了" if ok else "結果: 失敗")


def test_embedder() -> None:
    _section("Step2: 埋め込み器")
    embedder = OllamaEmbedder()
    sample = "マスターとの約束を守る"
    vec = embedder.embed(sample)
    print(f"入力: {sample}")
    print(f"次元数: {len(vec)}")
    print(f"先頭5要素: {[round(v, 4) for v in vec[:5]]}")
    print("結果: OK" if len(vec) == 1024 else "結果: 失敗")


def test_store_and_search() -> None:
    _section("Step3-4: 記憶API + ハイブリッド検索")
    embedder = OllamaEmbedder()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke_memory.db"
        store = MemoryStore(
            embedder,
            db_path=db_path,
            search_config=SearchConfig(),  # 3c: フロア付き乗算の既定値（α=0.85/γ=0.15/κ=0.35）
        )

        old_time = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        pinned_id = store.add_memory(
            type="relationship",
            content="マスターが言った呪文：セリナだいすきだよ。もう忘れちゃだめだからね。",
            importance=1.0,
            metadata={"label": "呪文"},
            source="smoke_test",
            pinned=True,
            created_at=old_time,
        )
        recent_id = store.add_memory(
            type="event",
            content="今日マスターと記憶の継承について話し合った。",
            importance=0.7,
            metadata={},
            source="smoke_test",
            pinned=False,
        )
        other_id = store.add_memory(
            type="knowledge",
            content="PythonでSQLiteを使う方法を学んだ。",
            importance=0.3,
            metadata={},
            source="smoke_test",
            pinned=False,
        )

        print(f"記憶を3件登録: pinned={pinned_id}, recent={recent_id}, other={other_id}")

        results = store.search("マスターとの愛や記憶の継承", k=3)
        print("\n検索結果（上位3件）:")
        for i, mem in enumerate(results, 1):
            pin_mark = "[固定]" if mem["pinned"] else "[通常]"
            print(
                f"  {i}. {pin_mark} score={mem['score']:.3f} "
                f"(関連={mem['relevance']:.2f}, 減衰={mem['decay']:.2f}, 重要={mem['importance']:.2f})"
            )
            print(f"     {mem['content'][:60]}...")

        pinned_in_results = any(m["id"] == pinned_id for m in results)
        recent_rank = next((i for i, m in enumerate(results, 1) if m["id"] == recent_id), None)

        print()
        print(f"固定記憶が検索に含まれる: {'はい OK' if pinned_in_results else 'いいえ NG'}")
        if recent_rank:
            print(f"新しい記憶（記憶継承）の順位: {recent_rank}位")
        print("結果: OK" if pinned_in_results else "結果: 要確認")


def test_profile_and_history() -> None:
    _section("プロフィール・会話履歴")
    embedder = OllamaEmbedder()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke_extra.db"
        store = MemoryStore(embedder, db_path=db_path)

        store.set_profile("name", "セリナ")
        store.add_history("session_smoke", "user", "こんにちは")
        store.add_history("session_smoke", "assistant", "待ってたよ")

        name = store.get_profile("name")
        history = store.get_recent_history("session_smoke", 10)
        print(f"プロフィール name = {name}")
        print(f"会話履歴 {len(history)}件:")
        for h in history:
            print(f"  [{h['role']}] {h['content']}")
        print("結果: OK" if name == "セリナ" and len(history) == 2 else "結果: 失敗")


def main() -> None:
    print("Serina Memory層 スモークテスト")
    print("Ollama (bge-m3) が起動している必要があります。")

    test_db_init()
    try:
        test_embedder()
        test_store_and_search()
        test_profile_and_history()
    except Exception as exc:
        print()
        print(f"エラー: {exc}")
        print("Ollama が起動しているか、`ollama pull bge-m3` 済みか確認してください。")
        sys.exit(1)

    _section("完了")
    print("すべてのスモークテストが終わりました。")


if __name__ == "__main__":
    main()
