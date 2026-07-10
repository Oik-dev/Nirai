""".env読み込みのテスト（実キーは使わず一時ファイルで検証）"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.env import get_gemini_api_key, load_env


def test_load_env_parses_key_value_and_ignores_comments() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text("# comment\nGEMINI_API_KEY=dummy_test_key\n\n", encoding="utf-8")
        values = load_env(env_path)
        assert values["GEMINI_API_KEY"] == "dummy_test_key"


def test_get_gemini_api_key_raises_when_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text("OTHER_VAR=x\n", encoding="utf-8")
        try:
            get_gemini_api_key(env_path)
            raise AssertionError("キー未設定で例外が出なかった")
        except RuntimeError:
            pass


def main() -> None:
    tests = [test_load_env_parses_key_value_and_ignores_comments, test_get_gemini_api_key_raises_when_missing]
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
