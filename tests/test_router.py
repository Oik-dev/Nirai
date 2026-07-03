"""スライス2（ルーティング・時刻注入）の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import CoreConfig
from serina.core.runtime import Core
from serina.memory.store import MemoryStore
from serina.skills.distill_intent import DistillIntentSkill
from serina.tests.test_reflection import FakeEmbedder, FakeSkill


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(FakeEmbedder(), db_path=tmp)


def test_distill_intent_patterns() -> None:
    s = DistillIntentSkill()
    for text in ("今日の話を整理して", "これまでの会話、まとめてくれる？",
                 "今回のこと日記にしておいて", "会話を整理して"):
        assert s.can_handle(text), f"検知漏れ: {text}"
    for text in ("今日は整理整頓した", "話の続きをしよう", "まとめサイト見てた"):
        assert not s.can_handle(text), f"誤検知: {text}"


def test_router_priority() -> None:
    store = _fresh_store()
    store.create_session("s1")
    core = Core(store, "p", [DistillIntentSkill(), FakeSkill()], config=CoreConfig())
    result = core.turn("s1", "今日の話を整理して")
    assert result["skill"] == "distill", f"蒸留インテントに回っていない: {result['skill']}"
    assert "整理して" in result["reply"]
    result = core.turn("s1", "おはよう、今日も頑張ろう")
    assert result["skill"] == "fake", "通常会話がキャッチオールに落ちていない"


def test_time_injection() -> None:
    store = _fresh_store()
    store.create_session("s1")
    core = Core(store, "p", [FakeSkill()], config=CoreConfig())
    core.turn("s1", "こんにちは")
    system = FakeSkill.last_ctx.system_prompt
    assert "## 現在" in system and "現在時刻:" in system, "現在時刻が注入されていない"

    cfg = CoreConfig(inject_time=False)
    core2 = Core(store, "p", [FakeSkill()], config=cfg)
    core2.turn("s1", "こんにちは")
    assert "現在時刻:" not in FakeSkill.last_ctx.system_prompt, "OFF設定が効いていない"


def main() -> None:
    tests = [test_distill_intent_patterns, test_router_priority, test_time_injection]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
