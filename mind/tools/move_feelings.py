"""気持ちの作り直しの E3（docs/plans/気持ちの作り直し.md §2「移し方」）。前の気持ちの仕組みの状態を、1回だけ新しい形へ移す。

    mind\\.venv\\Scripts\\python -m mind.tools.move_feelings --idea <イデア>           （見るだけ。何も書かない）
    mind\\.venv\\Scripts\\python -m mind.tools.move_feelings --idea <イデア> --apply   （移す）

移すもの（数だけを読み、記録やページの言葉は読まない）：
1. 今の気分（プルチックの8軸）と欲求の水準 → 体の芯とつながり。気持ちの記録の最初の1行（kind = "moved"）にする。
   時刻は前の状態を最後に記録した時刻なので、それからの時間で、新しい仕組みのとおりに落ち着き、つながりが減る。
   - 気分の平常からのずれ（気分 − 平常値）を、遅い層のずれに。情動の気分からのずれを、速い層のずれに。
   - 8軸それぞれの快・不快と高ぶりの座標は、感情語の標準の辞書 NRC VAD Lexicon v2.1（Mohammad 2025。-1〜1）の値。
     ずれの大きさの和が1を超えたら、和で割る（端を超えない）。
   - 欲求の水準（会いたさ）は、つながりの欠け（つながり＝1−水準）。
2. 記憶のページの8軸の気持ち → 芯の数（affect）を1回だけ計算して書き足す。8軸は本人が書いたものなので書き換えない。
   快・不快＝8軸の強さで重みを付けた辞書の快・不快。高ぶり＝1日のリズムの真ん中から、8軸の強さの平均の分だけ上へ
   （前の仕組みの「心の動きの強さ」と同じ順になる。記憶の強さの順位は 2026-10-03 の記憶テストでこの順のまま決めたもの。
   辞書の高ぶりを使うと、信頼や悲しみが「落ち着いた感情」として下がり、順位がほぼ入れ替わってしまった）。
   書き足す前に、すべてのページが読み書きでそのまま戻ることを確かめる（戻らないページがあれば、何も書かずに止まる）。
3. 前の状態ファイル（data/ の emotion_state.json・desire_state.json・relationship_state.json）を lifelog/legacy/ へ移す。

気持ちの記録がもうあれば、何もしない（1回だけ）。役目を終えたら消す（Git の履歴に残る）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from mind.core.config import load_thresholds
from mind.core.feeling.body import Body, FeelingParams, sense, settle
from mind.core.idea import Idea
from mind.core.lifelog import FeelingLog
from mind.core.memory.page import Page, dumps, load_pages, write_page

# NRC VAD Lexicon v2.1（Mohammad 2025）の、8つの感情語の valence・arousal（-1〜1）。
# 前の仕組みの軸の名前（状態ファイルは日本語、ページは英語）→ 座標
NRC_VAD = {
    "joy": (0.960, 0.648),
    "trust": (0.776, 0.094),
    "fear": (-0.854, 0.680),
    "surprise": (0.750, 0.750),
    "sadness": (-0.896, -0.424),
    "disgust": (-0.896, 0.550),
    "anger": (-0.666, 0.730),
    "anticipation": (0.396, 0.078),
}
AXES_JA = {"喜び": "joy", "信頼": "trust", "恐れ": "fear", "驚き": "surprise",
           "悲しみ": "sadness", "嫌悪": "disgust", "怒り": "anger", "期待": "anticipation"}
OLD_STATE_FILES = ("emotion_state.json", "desire_state.json", "relationship_state.json")
LEGACY_FOLDER = "気持ちの状態_2026-10"  # lifelog/legacy/ の下


def toward(amounts: dict[str, float]) -> tuple[float, float]:
    """8軸の量（英語の軸名 → 量。負もよい）を、快・不快と高ぶりの向きにする。量の大きさの和が1を超えたら和で割る。"""
    total = sum(abs(v) for v in amounts.values())
    if total == 0:
        return 0.0, 0.0
    scale = max(1.0, total)
    valence = sum(v * NRC_VAD[axis][0] for axis, v in amounts.items()) / scale
    arousal = sum(v * NRC_VAD[axis][1] for axis, v in amounts.items()) / 2 / scale  # 高ぶりは 0〜1 なので半分の幅
    return valence, arousal


def _ja(values: dict | None) -> dict[str, float]:
    return {AXES_JA[k]: float(v) for k, v in (values or {}).items() if k in AXES_JA}


def moved_body(emotion: dict | None, desire: dict | None, params: FeelingParams) -> Body:
    """前の状態（emotion_state.json・desire_state.json の中身）から、体の芯とつながり。"""
    mood, affect, baseline = _ja((emotion or {}).get("mood")), _ja((emotion or {}).get("affect")), _ja((emotion or {}).get("baseline"))
    slow_v, slow_a = toward({axis: mood.get(axis, 0.0) - baseline.get(axis, 0.0) for axis in NRC_VAD})
    fast_v, fast_a = toward({axis: affect.get(axis, 0.0) - mood.get(axis, 0.0) for axis in NRC_VAD})
    level = (desire or {}).get("level")
    connection = params.initial_connection if level is None else min(1.0, max(0.0, 1.0 - float(level)))
    return Body(fast_valence=fast_v, fast_arousal=fast_a, slow_valence=slow_v, slow_arousal=slow_a, connection=connection)


def moved_at(emotion: dict | None, desire: dict | None, now: datetime) -> datetime:
    """前の状態を最後に記録した時刻（なければ今）。"""
    for state in (emotion, desire):
        raw = (state or {}).get("last_tick_at")
        if raw:
            at = datetime.fromisoformat(raw)
            return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return now


def page_affect(page: Page, params: FeelingParams) -> dict[str, float] | None:
    """ページの8軸の気持ちから芯の数。気持ちがなければ None。"""
    amounts = {axis: float(v) for axis, v in page.feeling.items() if axis in NRC_VAD}
    if not any(amounts.values()):
        return None
    valence, _arousal = toward(amounts)
    strength = sum(amounts.values()) / len(NRC_VAD)  # 前の仕組みの心の動きの強さ（8軸の平均）
    return {
        "valence": round(min(1.0, max(-1.0, valence)), 3),
        "arousal": round(min(1.0, max(0.0, params.arousal_mid + (1 - params.arousal_mid) * strength)), 3),
    }


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def plan(idea: Idea, params: FeelingParams, now: datetime) -> dict:
    """何をどう移すか（まだ書かない）。"""
    emotion = _read_json(idea.data / "emotion_state.json")
    desire = _read_json(idea.data / "desire_state.json")
    body = moved_body(emotion, desire, params)
    at = moved_at(emotion, desire, now)
    pages, unchanged = [], []
    for page in load_pages(idea.memory):
        original = page.path_in(idea.memory).read_text(encoding="utf-8")
        if dumps(page) != original:
            unchanged.append(page.id)  # 読み書きでそのまま戻らない（このままでは書き足せない）
        affect = page_affect(page, params) if page.affect is None else None
        if affect is not None:
            pages.append(replace(page, affect=affect))
    return {
        "row": {"ts": at.astimezone(timezone.utc).isoformat(), "kind": "moved", "source": [], "feeling": "",
                "evaluation": None, "after": body.as_record()},
        "body": body,
        "at": at,
        "pages": pages,
        "not_round_trip": unchanged,
        "state_files": [name for name in OLD_STATE_FILES if (idea.data / name).exists()],
    }


def apply(idea: Idea, moving: dict) -> None:
    if moving["not_round_trip"]:
        raise RuntimeError(f"読み書きでそのまま戻らないページがある（何も書かずに止める）: {moving['not_round_trip'][:5]}")
    FeelingLog(idea.feeling).append(moving["row"])
    for page in moving["pages"]:
        write_page(idea.memory, page)
    legacy = idea.legacy / LEGACY_FOLDER
    legacy.mkdir(parents=True, exist_ok=True)
    for name in moving["state_files"]:
        os.replace(idea.data / name, legacy / name)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m mind.tools.move_feelings")
    parser.add_argument("--idea", required=True, help="イデアのフォルダー")
    parser.add_argument("--apply", action="store_true", help="移す（付けなければ見るだけ）")
    args = parser.parse_args()
    idea = Idea.open(args.idea)
    params = load_thresholds().feeling
    if next(FeelingLog(idea.feeling).rows(), None) is not None:
        print("気持ちの記録がもうある。移すのは1回だけなので、何もしない。")
        return
    now = datetime.now(timezone.utc)
    moving = plan(idea, params, now)
    body, at = moving["body"], moving["at"]
    then, today = sense(body, at, params), sense(settle(body, (now - at).total_seconds(), params), now, params)
    print(f"最初の1行：{at.isoformat()} に移す（それからの時間で落ち着く）")
    print(f"  そのとき：快・不快 {then.valence:+.2f}・高ぶり {then.arousal:.2f}・つながり {then.connection:.2f}")
    print(f"  今：      快・不快 {today.valence:+.2f}・高ぶり {today.arousal:.2f}・つながり {today.connection:.2f}")
    print(f"芯の数を書き足すページ：{len(moving['pages'])}")
    print(f"読み書きでそのまま戻らないページ：{len(moving['not_round_trip'])} {moving['not_round_trip'][:5]}")
    print(f"lifelog/legacy/{LEGACY_FOLDER}/ へ移す状態ファイル：{moving['state_files']}")
    if not args.apply:
        print("（見るだけ。移すときは --apply）")
        return
    apply(idea, moving)
    print("移した。索引は build_memory index で作り直す。")


if __name__ == "__main__":
    main()
