"""実運用向けCore組み立て(create_core)のテスト。設計書 §5.5-4, §3.1。

LLM不要（ネットワーク呼び出しはしない。Brainのインスタンス化と構造のみ検査する）。

2026-07-18: Brain構成刷新（合意台帳 §9）でQwen単一運用へ。create_coreはgemini_api_key
引数を持たない。登録簿はserina-qwen35-unc（primary/local）1行のみ。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.qwen.adapter import QwenAdapter
from serina.core.factory import create_core
from serina.core.runtime import Core


def _tmp_paths() -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp())
    return tmp / "test_memory.db", tmp / "test_chore_box.db"


def test_create_core_builds_full_core() -> None:
    memory_db_path, chore_box_path = _tmp_paths()

    core = create_core(memory_db_path=memory_db_path, chore_box_path=chore_box_path)

    assert isinstance(core, Core)
    assert core.persona_text  # prompt/persona.mdが読み込めている
    assert core.absolute_rules  # prompt/boundary.mdが読み込めている
    assert core.thresholds is not None
    assert core.memory_store is not None
    assert core.chore_box is not None
    assert {e.name for e in core.registry} == {"serina-qwen35-unc"}
    assert isinstance(core.brains["serina-qwen35-unc"], QwenAdapter)
    assert core.gemini_advisor is not None


def test_create_core_uses_default_paths_when_not_given() -> None:
    # デフォルト経路(data/serina_memory.db, data/chore_box.db)を指定しなくても構築できる
    # (実DBには触れない。MemoryStore/ChoreBoxはコンストラクタでスキーマ作成するだけ)
    memory_db_path, chore_box_path = _tmp_paths()
    core = create_core(memory_db_path=memory_db_path, chore_box_path=chore_box_path)
    assert core is not None


def test_build_brain_rejects_unknown_adapter() -> None:
    """未知のadapter種別は明示的にValueErrorで弾く（config/brains.toml誤記の早期検知）。"""
    from serina.core.config import load_thresholds
    from serina.core.factory import _build_brain
    from serina.core.routing.registry import BrainEntry

    bad_entry = BrainEntry("x", "unknown", "local", "primary", -1, -1, "small")
    try:
        _build_brain(bad_entry, load_thresholds())
    except ValueError:
        pass
    else:
        raise AssertionError("未知のadapter種別はValueErrorであるべき")


def main() -> None:
    tests = [
        test_create_core_builds_full_core,
        test_create_core_uses_default_paths_when_not_given,
        test_build_brain_rejects_unknown_adapter,
    ]
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
