"""実運用向けCore組み立て(create_core)のテスト。設計書 §5.5-4, §3.1。

LLM不要（ネットワーク呼び出しはしない。Brainのインスタンス化と構造のみ検査する）。

2026-07-18: Brain構成刷新（合意台帳 §9）でBrain単一運用へ。create_coreはgemini_api_key
引数を持たない。登録簿はserina-gemma4-unc（primary/local）1行のみ。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.factory import create_core
from mind.core.runtime import Core


def _tmp_paths() -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp())
    return tmp / "test_memory.db", tmp / "test_chore_box.db"


def test_create_core_builds_full_core() -> None:
    memory_db_path, chore_box_path = _tmp_paths()

    core = create_core(memory_db_path=memory_db_path, chore_box_path=chore_box_path)

    assert isinstance(core, Core)
    assert core.persona_text  # 魂の persona/ から結合できている
    assert core.absolute_rules  # role=absolute_rules（06_boundary.md）
    assert core.thresholds is not None
    assert core.memory_store is not None
    assert core.chore_box is not None
    assert {e.name for e in core.registry} == {"serina-gemma4-unc"}
    assert isinstance(core.brains["serina-gemma4-unc"], OllamaAdapter)
    assert core.gemini_advisor is not None
    assert core.change_log is None


def test_create_core_wires_change_log() -> None:
    """I-6: create_core に change_log を渡せる。"""
    from mind.core.memory.protection import ChangeLog

    memory_db_path, chore_box_path = _tmp_paths()
    change_log = ChangeLog(Path(tempfile.mkdtemp()) / "changes.jsonl")
    core = create_core(
        memory_db_path=memory_db_path,
        chore_box_path=chore_box_path,
        change_log=change_log,
    )
    assert core.change_log is change_log


def test_create_core_uses_default_paths_when_not_given() -> None:
    # デフォルト経路(data/serina_memory.db, data/chore_box.db)を指定しなくても構築できる
    # (実DBには触れない。MemoryStore/ChoreBoxはコンストラクタでスキーマ作成するだけ)
    memory_db_path, chore_box_path = _tmp_paths()
    core = create_core(memory_db_path=memory_db_path, chore_box_path=chore_box_path)
    assert core is not None


def test_build_brain_rejects_unknown_adapter() -> None:
    """未知のadapter種別は明示的にValueErrorで弾く（config/brains.toml誤記の早期検知）。"""
    from mind.core.config import load_thresholds
    from mind.core.factory import _build_brain
    from mind.core.routing.registry import BrainEntry

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
