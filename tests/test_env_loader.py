""".env読み込みのテスト（実キーは使わず一時ファイルで検証）

2026-07-18: Brain構成刷新（合意台帳 §9）でクラウドAPIキー配線（get_gemini_api_key）は
撤去済み。load_env自体はキー名に依存しない汎用パーサのため引き続き検証する。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.env import load_env


def test_load_env_parses_key_value_and_ignores_comments() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text("# comment\nSOME_KEY=dummy_test_value\n\n", encoding="utf-8")
        values = load_env(env_path)
        assert values["SOME_KEY"] == "dummy_test_value"


def test_load_env_returns_empty_dict_when_file_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing_path = Path(tmp) / ".env"
        assert load_env(missing_path) == {}


def main() -> None:
    tests = [test_load_env_parses_key_value_and_ignores_comments, test_load_env_returns_empty_dict_when_file_missing]
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
