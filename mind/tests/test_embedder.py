"""埋め込み(bge-m3)のテスト。新規実装（旧connectors/embedder.pyは参照しない）。

実際のOllama呼び出しはinjectableなcall_fnで差し替え、ネットワークに依存しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.embedder import EmbedderError, OllamaEmbedder


def test_embed_returns_vector_from_call_fn() -> None:
    def fake_call(model: str, text: str) -> list[float]:
        assert model == "bge-m3"
        assert text == "テスト文"
        return [0.1, 0.2, 0.3]

    embedder = OllamaEmbedder(call_fn=fake_call)
    vector = embedder.embed("テスト文")
    assert vector == [0.1, 0.2, 0.3]


def test_embed_raises_on_empty_vector() -> None:
    embedder = OllamaEmbedder(call_fn=lambda model, text: [])
    try:
        embedder.embed("テスト文")
        raise AssertionError("空ベクトルが通ってしまった")
    except EmbedderError:
        pass


def main() -> None:
    tests = [test_embed_returns_vector_from_call_fn, test_embed_raises_on_empty_vector]
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
