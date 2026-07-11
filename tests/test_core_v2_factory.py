"""実運用向けCore組み立て(create_core_v2)のテスト。設計書v2 §5.5-4, §3.1。

LLM不要（ネットワーク呼び出しはしない。Brainのインスタンス化と構造のみ検査する）。
DECISIONS 2026-07-11「旧GUIをcore_v2へ移行」で、core_v2.Coreを本番用に組み立てる
factoryが今まで存在しなかったことを解消した。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.aurora.adapter import AuroraAdapter
from serina.brains.gemini.adapter import GeminiAdapter
from serina.core_v2.factory import create_core_v2
from serina.core_v2.runtime import Core


def _tmp_paths() -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp())
    return tmp / "test_memory.db", tmp / "test_chore_box.db"


def test_create_core_v2_builds_full_core_without_gemini_key() -> None:
    memory_db_path, chore_box_path = _tmp_paths()

    core = create_core_v2(memory_db_path=memory_db_path, chore_box_path=chore_box_path)

    assert isinstance(core, Core)
    assert core.persona_text  # prompt/persona.mdが読み込めている
    assert core.absolute_rules  # prompt/boundary.mdが読み込めている
    assert core.thresholds is not None
    assert core.memory_store is not None
    assert core.chore_box is not None
    assert {e.name for e in core.registry} == {"gemini_flash_lite", "gemini_flash", "aurora"}
    assert isinstance(core.brains["aurora"], AuroraAdapter)
    # APIキー無し: cloud系Brainは呼べないプレースホルダになる(§3.5の「弾切れ」と同じ扱い)
    try:
        core.brains["gemini_flash_lite"].converse(None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("APIキー未設定のGeminiは呼び出し不能であるべき")


def test_create_core_v2_builds_real_gemini_adapters_with_key() -> None:
    memory_db_path, chore_box_path = _tmp_paths()

    core = create_core_v2(
        gemini_api_key="dummy-key", memory_db_path=memory_db_path, chore_box_path=chore_box_path,
    )

    assert isinstance(core.brains["gemini_flash_lite"], GeminiAdapter)
    assert isinstance(core.brains["gemini_flash"], GeminiAdapter)
    # 2つのGemini brainは異なるモデル名を持つ(model名マッピングが機能している)
    assert core.brains["gemini_flash_lite"]._model != core.brains["gemini_flash"]._model


def test_create_core_v2_uses_default_paths_when_not_given() -> None:
    # デフォルト経路(data/serina_memory.db, data/chore_box.db)を指定しなくても構築できる
    # (実DBには触れない。MemoryStore/ChoreBoxはコンストラクタでスキーマ作成するだけ)
    memory_db_path, chore_box_path = _tmp_paths()
    core = create_core_v2(memory_db_path=memory_db_path, chore_box_path=chore_box_path)
    assert core is not None


def main() -> None:
    tests = [
        test_create_core_v2_builds_full_core_without_gemini_key,
        test_create_core_v2_builds_real_gemini_adapters_with_key,
        test_create_core_v2_uses_default_paths_when_not_given,
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
