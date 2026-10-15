""".env読み込みのテスト（実キーは使わず一時ファイルで検証）

load_env はキー名に依存しない汎用パーサ。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.env import load_env


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
