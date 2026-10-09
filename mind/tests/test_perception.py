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
    assert list(body_schema(CATALOG)) == ["expression", "gesture", "wish"]
    assert body_question(BodyCatalog()) == "" and body_schema(BodyCatalog()) == {}


def test_only_choices_from_the_catalog_flow() -> None:
    assert parse_body({"expression": "喜び", "gesture": "うなずく"}, CATALOG) == BodyChoice("喜び", "うなずく")
    assert parse_body({"expression": "なし", "gesture": "なし"}, CATALOG) == BodyChoice(expression="なし")
    assert parse_body({"expression": "そのまま", "gesture": "そのまま"}, CATALOG) is None
    assert parse_body({"expression": "怒り", "gesture": "跳ねる"}, CATALOG) is None
    assert parse_body({"expression": "驚き", "gesture": 1}, CATALOG) == BodyChoice(expression="驚き")
    assert parse_body({"gesture": "うなずく"}, BodyCatalog(expressions=("喜び",))) is None  # 聞いていない欄は流さない


def test_a_wish_is_a_separate_field_only_for_the_other_gesture() -> None:
    question = body_question(CATALOG)
    assert question.index("gesture:") < question.index("wish:")
    assert "ほかの動き" in body_schema(CATALOG)["gesture"]["enum"]
    assert body_schema(CATALOG)["wish"] == {"type": "string"}
    assert parse_body({"gesture": "ほかの動き", "wish": "手を振る"}, CATALOG) == BodyChoice(wish="手を振る")
    assert parse_body({"expression": "喜び", "gesture": "ほかの動き", "wish": "伸びる"}, CATALOG).fields() == {
        "expression": "喜び", "wish": "伸びる",
    }
    assert parse_body({"gesture": "うなずく", "wish": "手を振る"}, CATALOG) == BodyChoice(gesture="うなずく")
    assert parse_body({"gesture": "ほかの動き", "wish": ""}, CATALOG) is None
    for invalid in ("なし", "そのまま", "ほかの動き", "あ\nい", "あ" * 41, " 余分 ", "../outside", "a/b", ".hidden"):
        assert parse_body({"gesture": "ほかの動き", "wish": invalid}, CATALOG) is None
    assert body_schema(BodyCatalog(expressions=("喜び",))) == {
        "expression": {"type": "string", "enum": ["喜び", "そのまま", "なし"]},
    }
    assert BodyChoice(gesture="うなずく").fields() == {"gesture": "うなずく"}


def test_activity_is_only_asked_after_a_reply_and_never_has_keep_or_none() -> None:
    from mind.core.feeling.appraisal import appraisal_question, appraisal_schema
    from mind.core.perception import body_alone_question, body_alone_schema

    catalog = parse_catalog({
        "expressions": ["喜び"], "gestures": ["うなずく"],
        "activities": [" 海で泳ぐ ", "海で泳ぐ", "そのまま", "なし", "砂地で休む"],
    })
    assert catalog.activities == ("海で泳ぐ", "砂地で休む")
    assert list(appraisal_schema(catalog)["properties"])[:3] == ["expression", "gesture", "activity"]
    assert appraisal_schema(catalog)["properties"]["activity"]["enum"] == ["海で泳ぐ", "砂地で休む"]
    assert "activity: このあと、どこで何をするかは 海で泳ぐ / 砂地で休む" in appraisal_question(catalog)
    assert "activity" not in body_alone_schema(catalog)["properties"]
    assert "activity:" not in body_alone_question(catalog)
    assert parse_body({"activity": "海で泳ぐ"}, catalog, include_activity=True) == BodyChoice(activity="海で泳ぐ")
    assert parse_body({"activity": "海で泳ぐ"}, catalog) is None
    assert parse_body({"activity": "なし"}, catalog, include_activity=True) is None
    assert parse_body({"activity": "そのまま"}, catalog, include_activity=True) is None
    assert parse_body({"activity": "知らない場所"}, catalog, include_activity=True) is None
    assert BodyChoice(activity="砂地で休む").fields() == {"activity": "砂地で休む"}
