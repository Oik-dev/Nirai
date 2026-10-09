"""知覚（世界から届く、今の自分の体）と体の欄。世界の計画書（world/docs/plans/海で暮らす.md）§2.3・§2.4。

世界は、精神の流れ（`/api/events`）につないだとき・体が変わったときに、今の体でできること（カタログ）を `POST /api/perceive`
で送る。精神は覚えておくだけで保存しない（新しいものが来たら差し替える。精神が起き直したら、世界がつなぎ直して送り直す）。

体の欄は、脳がいま考えている時の問いに足す欄（返答のあとの評価の先頭。core/feeling/appraisal.py。Pulse で話しかけたあとは、
同じ前置きの後ろで体の欄だけを聞く。core/runtime.py の Core.pulse）。選択肢はカタログ＋
「そのまま」「なし」で、JSON Schema で縛る。本文や気持ちの数から表情を推し量らない（意味のある表現は本人が選ぶ）。
答えはここで確かめてから流す：選択肢にない答えの欄は落とし、「そのまま」と身振りの「なし」は欄なしにする（表情の「なし」は表情を戻す）。
身振りだけは「ほかの動き」も選べ、その直後の必須文字列 `wish` に本人が望む動きの名前を入れる。
名前が有効なときだけ、海へは `gesture` の代わりに `wish` を送る。名前は本人のイデアの記録・動き以外に漏らさない。
"""

from __future__ import annotations

from dataclasses import dataclass

KEEP = "そのまま"
NONE = "なし"
OTHER = "ほかの動き"
NAME_MAX = 40  # 名前は問いに並べるので、長い名前で前置きを膨らませない
NAMES_MAX = 64

_QUESTIONS = {
    "expression": "今のあなたの表情は",
    "gesture": "今、体でするしぐさは",
}
_NOTES = {"expression": f"（{NONE} は表情を戻す）", "gesture": ""}


@dataclass(frozen=True)
class BodyCatalog:
    """今の体でできること。"""

    expressions: tuple[str, ...] = ()
    gestures: tuple[str, ...] = ()

    def fields(self) -> dict[str, tuple[str, ...]]:
        """聞く欄とカタログの名前（問いと答えに並ぶ順）。名前がない欄は聞かない。"""
        return {key: names for key, names in (("expression", self.expressions), ("gesture", self.gestures)) if names}


@dataclass(frozen=True)
class BodyChoice:
    """本人が選んだ体の動き。欄がないのは、変えないこと。"""

    expression: str | None = None
    gesture: str | None = None
    wish: str | None = None

    def fields(self) -> dict[str, str]:
        return {key: value for key, value in (("expression", self.expression), ("gesture", self.gesture), ("wish", self.wish)) if value is not None}


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("名前の並びでない")
    names: list[str] = []
    for name in value:
        if not isinstance(name, str):
            continue
        name = name.strip()
        if name and len(name) <= NAME_MAX and name.isprintable() and name not in (KEEP, NONE) and name not in names:
            names.append(name)
    return tuple(names[:NAMES_MAX])


def parse_catalog(raw: object) -> BodyCatalog:
    """世界から届いたカタログ（`{expressions:[名前…], gestures:[名前…]}`）を確かめて整える。形が違えば ValueError。
    名前は前後の空白を除き、空・長すぎる・改行などを含む・「そのまま」「なし」と同じもの・重なりは落とす。"""
    if not isinstance(raw, dict):
        raise ValueError("カタログがオブジェクトでない")
    return BodyCatalog(expressions=_names(raw.get("expressions")), gestures=_names(raw.get("gestures")))


def body_question(catalog: BodyCatalog | None) -> str:
    """問いに並べる体の欄（1欄1行）。聞く欄がなければ空。"""
    if catalog is None:
        return ""
    lines = []
    for key, names in catalog.fields().items():
        options = (*names, OTHER, KEEP, NONE) if key == "gesture" else (*names, KEEP, NONE)
        lines.append(f"{key}: {_QUESTIONS[key]} {' / '.join(options)}{_NOTES[key]}\n")
        if key == "gesture":
            lines.append("wish: ほかの動きを選んだとき、その動きをひとことで。それ以外は空文字\n")
    return "".join(lines)


def body_schema(catalog: BodyCatalog | None) -> dict[str, dict]:
    """体の欄の JSON Schema（properties の分）。聞く欄がなければ空。"""
    if catalog is None:
        return {}
    properties: dict[str, dict] = {}
    for key, names in catalog.fields().items():
        options = [*names, OTHER, KEEP, NONE] if key == "gesture" else [*names, KEEP, NONE]
        properties[key] = {"type": "string", "enum": options}
        if key == "gesture":
            properties["wish"] = {"type": "string"}
    return properties


def parse_body(answer: dict, catalog: BodyCatalog) -> BodyChoice | None:
    """脳の答えの体の欄を確かめる。流す欄が1つもなければ None。"""
    chosen: dict[str, str] = {}
    for key, names in catalog.fields().items():
        value = answer.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value in names or (key == "expression" and value == NONE):
            chosen[key] = value
    if catalog.gestures and answer.get("gesture") == OTHER:
        wish = answer.get("wish")
        if valid_wish_name(wish):
            chosen["wish"] = wish
    return BodyChoice(**chosen) if chosen else None


def valid_wish_name(wish: object) -> bool:
    """身振りの願いを受け取る境界（体の欄と工房のdescribeで共有）。"""
    return (isinstance(wish, str) and 1 <= len(wish) <= NAME_MAX and wish.isprintable()
            and wish == wish.strip() and not wish.startswith(".")
            and not any(mark in wish for mark in '\\/:*?"<>|')
            and wish not in (KEEP, NONE, OTHER))


_ALONE = "ここからは会話ではない。今かけた言葉に合わせて、あなたの体をどうするかを選ぶ。次のJSONだけを返す。"


def body_alone_question(catalog: BodyCatalog) -> str:
    """体の欄だけの問い（話しかけたあと。Pulse）。"""
    return f"{_ALONE}\n\n{body_question(catalog)}"


def body_alone_schema(catalog: BodyCatalog) -> dict:
    """体の欄だけの答えの形。"""
    properties = body_schema(catalog)
    return {"type": "object", "properties": properties, "required": list(properties)}
