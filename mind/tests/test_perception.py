"""core/perception.py（世界から届く今の体と、体の欄）のテスト。世界の計画書 §2.3・§2.4。

守るもの：
- 世界から届いた名前は入口で整える（形が違えば受け取らない。問いを壊す名前・「そのまま」「なし」と同じ名前は並べない）。
- 問いと答えの形は、カタログにある欄だけ。選択肢はカタログ＋「そのまま」「なし」。
- 答えは確かめてから流す：選択肢にない欄は落とし、「そのまま」と身振りの「なし」は欄なし（表情の「なし」は表情を戻す）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.perception import (
    NAME_MAX,
    NAMES_MAX,
    BodyCatalog,
    BodyChoice,
    body_question,
    body_schema,
    parse_body,
    parse_catalog,
)

CATALOG = BodyCatalog(expressions=("喜び", "驚き"), gestures=("うなずく",))


def test_the_catalog_is_cleaned_at_the_entry() -> None:
    catalog = parse_catalog({
        "expressions": [" 喜び ", "喜び", "", 3, "そのまま", "なし", "改\n行", "あ" * (NAME_MAX + 1), "驚き"],
        "gestures": [f"g{i}" for i in range(NAMES_MAX + 5)],
    })
    assert catalog.expressions == ("喜び", "驚き")
    assert catalog.gestures == tuple(f"g{i}" for i in range(NAMES_MAX))


@pytest.mark.parametrize("raw", [None, [], {"expressions": ["喜び"]}, {"expressions": "喜び", "gestures": []}])
def test_a_catalog_of_another_shape_is_refused(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_catalog(raw)


def test_the_question_and_schema_ask_only_the_fields_the_body_has() -> None:
    assert body_question(None) == "" and body_schema(None) == {}
    only_faces = BodyCatalog(expressions=("喜び",))
    assert body_question(only_faces) == "expression: 今のあなたの表情は 喜び / そのまま / なし（なし は表情を戻す）\n"
    assert body_schema(only_faces) == {"expression": {"type": "string", "enum": ["喜び", "そのまま", "なし"]}}
    assert list(body_schema(CATALOG)) == ["expression", "gesture"]
    assert body_question(BodyCatalog()) == "" and body_schema(BodyCatalog()) == {}


def test_only_choices_from_the_catalog_flow() -> None:
    assert parse_body({"expression": "喜び", "gesture": "うなずく"}, CATALOG) == BodyChoice("喜び", "うなずく")
    assert parse_body({"expression": "なし", "gesture": "なし"}, CATALOG) == BodyChoice(expression="なし")
    assert parse_body({"expression": "そのまま", "gesture": "そのまま"}, CATALOG) is None
    assert parse_body({"expression": "怒り", "gesture": "跳ねる"}, CATALOG) is None
    assert parse_body({"expression": "驚き", "gesture": 1}, CATALOG) == BodyChoice(expression="驚き")
    assert parse_body({"gesture": "うなずく"}, BodyCatalog(expressions=("喜び",))) is None  # 聞いていない欄は流さない
    assert BodyChoice(gesture="うなずく").fields() == {"gesture": "うなずく"}
