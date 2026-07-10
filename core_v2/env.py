"""`.env`（git管理外）からのシークレット読み込み。設計書v2 §5.5-4: キーをコード・ログに含めない。"""

from __future__ import annotations

from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path | None = None) -> dict[str, str]:
    target = path or DEFAULT_ENV_PATH
    if not target.exists():
        return {}
    values: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def get_gemini_api_key(path: Path | None = None) -> str:
    key = load_env(path).get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError(".envにGEMINI_API_KEYが設定されていない")
    return key
