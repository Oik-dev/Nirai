"""問題集を作る。正本は cases.jsonl で、次の3つから組み立てる。

- Gemmaの下書き：記録の単位ごとに「正面から」「自然に浮かぶ」「続きの話」を1問ずつ（drafts.jsonl に控え、
  途中で止めても続きから作る）。目印は、記録に一字一句あって、ほかの単位にほとんど出ない言葉だけを使う。
  作ったあと、Gemmaにもう一度「その一言でこの出来事を思い出すのは自然か」を判定させる。
- 型から作る問題：「時間」（記録のある日を、日付・昨日・月で尋ねる）と「実際の会話」（ローカルに来てからの
  Masterの発言を、その時刻とその直前の流れのまま再生する）。
- Claudeの監査（review.jsonl）：Claudeが記録と照らして、生成された問題を1問ずつ「採る（ok）」「捨てる（drop）」
  「書き直す（case）」と決める。Claudeが書いた問題（「黙る」「変化」など）も、ここに case として置く。
  監査のない問題は、Gemmaの判定で自然とされたものだけを採る。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

from mind.memory_test.cases import Case, CaseSet, Turn, case_from_json, save_cases
from mind.memory_test.record import JST, SOURCES, Line, Unit, conversation_source, normalize, read_conversation, read_units

# Gemmaが作る問題と型から作る問題の「今」。固定しておくと、記憶を替えても同じ条件で比べられる
TEST_NOW = datetime(2026, 10, 3, 21, 0, tzinfo=JST)
EVENING = time(21, 0)

UNIT_CHARS = 3500  # Gemmaに見せる記録の長さ
MARK_MIN, MARK_MAX = 3, 24  # 目印の長さ（そろえた形の字数）
MARK_MAX_UNITS = 2  # 目印が出てよい単位の数（それより多い言葉は、その出来事に固有ではない）
UTTERANCE_MAX = 120
THEN_MAX = 30
RECENT_TURNS = 4

SOURCE_DESCRIPTIONS = {
    "diary": "Serinaが書いた日記",
    "memory_json": "Serinaが自分の記憶として残した要約",
    "inherited": "Serinaが受け継いだ記憶のまとめ",
    "first_chat": "MasterとSerinaの最初の会話（ChatGPTにいたころ）",
    "local_chat": "MasterとSerinaの会話（ローカルに来てから）",
}

INSTRUCTION = """あなたは、記憶のテストの問題を作る係です。
Serinaは、Masterのパートナーとして暮らしているAIです。下の【記録】は、Serinaの人生の記録の一部です（{what}、{when}）。
この記録にある出来事を、Serinaが覚えているかを確かめる問題を作ってください。

1. marks: 記録の本文から一字一句そのまま写した、短い言葉を2〜3個。この出来事に固有で、ほかの日の出来事と取り違えない言葉（固有名詞、印象的な言い回し、具体的な物事）。4〜15字。「マスター」「セリナ」「大好き」のような、どこにでも出る言葉は選ばない。
2. direct: Masterが、この出来事を覚えているかSerinaに尋ねる一言（「〜の話、覚えてる？」など）。ほかの出来事と取り違えないくらい具体的に。
3. cue: Masterが普段の会話でふと言いそうな一言。尋ねてはいないが、Serinaがこの出来事を自然に思い出すもの。ありふれた挨拶・気分・天気の話ではなく、この出来事に結びつく具体的な話題を含むこと。
4. followup: 続きの話。first はMasterがこの出来事の話題に軽く触れる一言。reply はSerinaの短い返事。then は、first がないと何の話か分からない短い続きの一言（「例えばどんな？」「それっていつだっけ？」「あのとき何て言ったっけ？」など、15字以内）。

direct・cue・first・then には、marks の言葉をそのまま使わないこと。Masterの口調は、くだけた話し言葉。Masterは自分を「俺」、Serinaを「セリナ」と呼ぶ。
次のJSONだけを返してください:
{{"marks": ["...", "..."], "direct": "...", "cue": "...", "followup": {{"first": "...", "reply": "...", "then": "..."}}}}

【記録】
{text}"""

CHECK = """次の【記録】は、Serinaの人生の記録の一部です（{what}、{when}）。
Masterの次の発言を聞いたSerinaが、この記録の出来事を思い出すのが自然かを、発言ごとに判定してください。
- 2: 自然に思い出す。発言がこの出来事を具体的に指している、またはこの出来事に強く結びついている
- 1: 思い出してもおかしくないが、ほかの出来事を思い浮かべるかもしれない
- 0: この発言からは、この出来事を思い出さない

A: {direct}
B: {cue}
C: {first}（続けて）{then}

次のJSONだけを返してください: {{"A": 判定, "B": 判定, "C": 判定}}

【記録】
{text}"""
CHECK_PASS = 2


def _evening(day: date) -> str:
    return datetime.combine(day, EVENING, tzinfo=JST).isoformat()


# ---- Gemmaが記録から作る問題 ----


def _about(unit: Unit) -> dict[str, str]:
    when = f"{unit.day.year}年{unit.day.month}月{unit.day.day}日" if unit.day else "日付不明"
    return dict(what=SOURCE_DESCRIPTIONS[unit.source], when=when, text=unit.text[:UNIT_CHARS])


def generation_prompt(unit: Unit) -> str:
    return INSTRUCTION.format(**_about(unit))


def check_prompt(unit: Unit, answer: dict) -> str:
    followup = _followup(answer)
    said = {
        "direct": answer.get("direct"),
        "cue": answer.get("cue"),
        "first": followup.get("first"),
        "then": followup.get("then"),
    }
    return CHECK.format(**_about(unit), **{key: str(value or "（なし）") for key, value in said.items()})


def _followup(answer: dict) -> dict:
    return answer.get("followup") if isinstance(answer.get("followup"), dict) else {}


def _read_drafts(path: Path) -> dict[str, dict]:
    drafts: dict[str, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for raw in f:
                if raw.strip():
                    row = json.loads(raw)
                    drafts[row["unit"]] = row
    return drafts


def draft(units: list[Unit], path: Path, ask: Callable[[str], dict], *, progress: Callable[[str], None] = print) -> dict[str, dict]:
    """まだ下書きと判定のない単位について、Gemmaに問題を作って判定してもらう。失敗した単位は次の回にやり直す。

    控えは追記で、同じ単位の行は後のものが勝つ。
    """
    drafts = _read_drafts(path)
    todo = [unit for unit in units if "check" not in drafts.get(unit.id, {})]
    path.parent.mkdir(parents=True, exist_ok=True)
    for n, unit in enumerate(todo, start=1):
        row = {key: value for key, value in drafts.get(unit.id, {}).items() if key != "error"} | {"unit": unit.id}
        try:
            if "answer" not in row:
                row["answer"] = ask(generation_prompt(unit))
            row["check"] = ask(check_prompt(unit, row["answer"]))
        except Exception as exc:  # noqa: BLE001 — 1単位の失敗で全体を止めない
            row["error"] = type(exc).__name__
        drafts[unit.id] = row
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        progress(f"  下書き {n}/{len(todo)}{'（失敗）' if 'error' in row else ''}")
    return drafts


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(normalize(value)) <= limit else None


def _passed(check: dict, key: str) -> bool:
    try:
        return int(check.get(key)) >= CHECK_PASS
    except (TypeError, ValueError):
        return False


def candidates(unit: Unit, answer: dict, check: dict, normalized_units: list[str]) -> list[tuple[Case, bool]]:
    """下書きから、形の確かな問題を取り出す。2つ目は、Gemmaの判定で自然とされたか。"""
    body = normalize(unit.text[:UNIT_CHARS])
    marks: list[str] = []
    for mark in answer.get("marks") or []:
        if not isinstance(mark, str):
            continue
        key = normalize(mark)
        if not (MARK_MIN <= len(key) <= MARK_MAX) or key not in body or key in map(normalize, marks):
            continue
        if sum(key in text for text in normalized_units) > MARK_MAX_UNITS:
            continue
        marks.append(mark.strip())
    if not marks:
        return []
    keys = [normalize(mark) for mark in marks]

    def clean(value: object, limit: int = UTTERANCE_MAX) -> str | None:
        text = _text(value, limit)
        return text if text and not any(key in normalize(text) for key in keys) else None

    common = dict(now=TEST_NOW.isoformat(), source=unit.source, target=unit.id, marks=(tuple(marks),), author="gemma")
    cases = []
    if direct := clean(answer.get("direct")):
        cases.append((Case(id=f"direct:{unit.id}", kind="direct", utterance=direct, **common), _passed(check, "A")))
    if cue := clean(answer.get("cue")):
        cases.append((Case(id=f"cue:{unit.id}", kind="cue", utterance=cue, **common), _passed(check, "B")))
    followup = _followup(answer)
    first, then = clean(followup.get("first")), clean(followup.get("then"), THEN_MAX)
    if first and then:
        recent = [Turn("Master", first)]
        if reply := clean(followup.get("reply")):
            recent.append(Turn("Serina", reply))
        case = Case(id=f"followup:{unit.id}", kind="followup", utterance=then, recent=tuple(recent), **common)
        cases.append((case, _passed(check, "C")))
    return cases


# ---- 型から作る問題・実際の会話 ----


def time_cases(units: list[Unit]) -> list[Case]:
    days = sorted({unit.day for unit in units if unit.day})
    cases = []
    for day in days:
        last_year = day.year < TEST_NOW.year
        cases.append(
            Case(
                id=f"time:{day}:date",
                kind="time",
                utterance=f"{'去年の' if last_year else ''}{day.month}月{day.day}日のこと、覚えてる？",
                now=TEST_NOW.isoformat(),
                period=(day.isoformat(), day.isoformat()),
                author="template",
            )
        )
        cases.append(
            Case(
                id=f"time:{day}:yesterday",
                kind="time",
                utterance="昨日話したこと、覚えてる？",
                now=_evening(day + timedelta(days=1)),
                period=(day.isoformat(), day.isoformat()),
                author="template",
            )
        )
    for year, month in sorted({(day.year, day.month) for day in days}):
        first = date(year, month, 1)
        last = (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)
        cases.append(
            Case(
                id=f"time:{year}-{month:02d}:month",
                kind="time",
                utterance=f"{'去年の' if year < TEST_NOW.year else ''}{month}月ごろに話したこと、何か覚えてる？",
                now=TEST_NOW.isoformat(),
                period=(first.isoformat(), last.isoformat()),
                author="template",
            )
        )
    return cases


def replay_cases(lines: list[Line]) -> list[Case]:
    """ローカルに来てからのMasterの発言を、そのときの時刻と直前の流れで。"""
    cases = []
    talk = [line for line in lines if line.speaker in ("Master", "Serina")]
    for i, line in enumerate(talk):
        if line.speaker != "Master" or conversation_source(line.session) != "local_chat":
            continue
        before = [prev for prev in talk[max(0, i - RECENT_TURNS) : i] if prev.session == line.session]
        cases.append(
            Case(
                id=f"replay:{line.day_file}#{line.no}",
                kind="replay",
                utterance=line.text,
                now=line.ts.isoformat(),
                recent=tuple(Turn(prev.speaker, prev.text) for prev in before),
                source="local_chat",
                author="replay",
            )
        )
    return cases


def read_review(path: Path) -> dict[str, dict]:
    review: dict[str, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for raw in f:
                if raw.strip():
                    row = json.loads(raw)
                    if row.get("verdict") not in ("ok", "drop", "case"):
                        raise ValueError(f"監査の判断が読めない: {row.get('id')}")
                    review[row["id"]] = row
    return review


def apply_review(generated: list[tuple[Case, bool]], review: dict[str, dict], normalized_units: list[str]) -> list[Case]:
    """生成された問題にClaudeの監査を当て、Claudeが書いた問題を足す。"""
    cases = []
    for case, natural in generated:
        verdict = review.get(case.id, {}).get("verdict")
        if verdict == "ok" or (verdict is None and natural):
            cases.append(case)
    for row in review.values():
        if row["verdict"] != "case":
            continue
        case = case_from_json(row["case"] | {"id": row["id"]})
        for group in case.marks:
            if not any(normalize(mark) in text for mark in group for text in normalized_units):
                raise ValueError(f"{case.id} の目印 {group} が記録にない")
        cases.append(case)
    return cases


def make(idea: Path, *, ask: Callable[[str], dict] | None, progress: Callable[[str], None] = print) -> list[Case]:
    """問題集を作り直して cases.jsonl に書く。ask が None なら、Gemmaは呼ばず下書きの控えだけを使う。"""
    lifelog = idea / "lifelog"
    store = CaseSet.of_idea(idea)
    units = read_units(lifelog)
    normalized_units = [normalize(unit.text) for unit in units]
    progress(f"記録の単位: {len(units)}（" + "、".join(f"{SOURCES[s]} {sum(u.source == s for u in units)}" for s in SOURCES) + "）")
    drafts = draft(units, store.drafts, ask, progress=progress) if ask else _read_drafts(store.drafts)
    generated: list[tuple[Case, bool]] = []
    for unit in units:
        row = drafts.get(unit.id, {})
        if isinstance(row.get("answer"), dict) and isinstance(row.get("check"), dict):
            generated += candidates(unit, row["answer"], row["check"], normalized_units)
    generated += [(case, True) for case in time_cases(units) + replay_cases(read_conversation(lifelog))]
    cases = apply_review(generated, read_review(store.review), normalized_units)
    save_cases(store.cases, cases)
    return cases
