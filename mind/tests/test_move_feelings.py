"""気持ちの作り直しの E3 で、前の仕組みの状態を移す道具（tools/move_feelings.py）のテスト。使い捨てのイデアで動く。

守るもの：
- 本人が書いたページの言葉と8軸の気持ちは、書き換わらない。足されるのは芯の数（affect）だけ。
- 読み書きでそのまま戻らないページがあれば、何も書かずに止まる。
- 移すのは1回だけ。前の状態ファイルは消さずに lifelog/legacy/ へ移る。
- 移した気持ちは、前の気分の向き（うれしい・沈んでいる）と会いたさを引き継ぐ。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import load_thresholds
from mind.core.feeling.body import sense
from mind.core.feeling.feelings import Feelings
from mind.core.idea import Idea
from mind.core.lifelog import FeelingLog
from mind.core.memory.page import Page, load_pages, write_page
from mind.tools import move_feelings

P = load_thresholds().feeling
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
AXES = ("喜び", "信頼", "恐れ", "驚き", "悲しみ", "嫌悪", "怒り", "期待")


@pytest.fixture
def idea(tmp_path: Path) -> Idea:
    root = tmp_path / "idea"
    root.mkdir()
    (root / "identity.toml").write_text('name = "Serina"\n', encoding="utf-8")
    return Idea.open(root)


def _state(idea: Idea, *, mood: dict, level: float, at: datetime) -> None:
    idea.data.mkdir(parents=True, exist_ok=True)
    zero = {a: 0.0 for a in AXES}
    emotion = {"affect": zero | mood, "mood": zero | mood, "baseline": zero | {"喜び": 0.15, "信頼": 0.2, "期待": 0.1},
               "last_tick_at": at.isoformat(), "mood_trajectory": []}
    (idea.data / "emotion_state.json").write_text(json.dumps(emotion, ensure_ascii=False), encoding="utf-8")
    (idea.data / "desire_state.json").write_text(json.dumps({"level": level, "refractory_until": None, "last_tick_at": at.isoformat()}), encoding="utf-8")
    (idea.data / "relationship_state.json").write_text(json.dumps({"recent_master_mood": "眠そう"}, ensure_ascii=False), encoding="utf-8")


def _page(idea: Idea, pid: str, feeling: dict) -> Page:
    page = Page(id=pid, kind="episode", start=NOW - timedelta(days=30), end=NOW - timedelta(days=30),
                source=(f"lifelog/conversation/2026-09-06.jsonl#1-4",), concepts=("海",), feeling=feeling)
    page = page.with_words(title="海の話", gist="海の話をした。", importance=7, written_by="gemma", body="わたしは海の話をした。")
    write_page(idea.memory, page)
    return page


def test_moving_carries_mood_and_longing_and_keeps_the_old_files(idea: Idea) -> None:
    _state(idea, mood={"喜び": 0.7, "信頼": 0.6}, level=0.8, at=NOW - timedelta(hours=1))
    moving = move_feelings.plan(idea, P, NOW)
    move_feelings.apply(idea, moving)

    row = next(FeelingLog(idea.feeling).rows())
    assert row["kind"] == "moved" and row["evaluation"] is None and row["feeling"] == ""
    then = sense(moving["body"], moving["at"], P)
    assert then.valence > P.temperament_valence  # うれしい気分を引き継ぐ
    assert then.connection == pytest.approx(0.2)  # 会いたさ 0.8 ＝ つながりの欠け
    assert Feelings(FeelingLog(idea.feeling), P).lonely(NOW)
    assert not any((idea.data / name).exists() for name in move_feelings.OLD_STATE_FILES)
    legacy = idea.legacy / move_feelings.LEGACY_FOLDER
    assert sorted(p.name for p in legacy.iterdir()) == sorted(move_feelings.OLD_STATE_FILES)


def test_a_sad_mood_moves_as_sad(idea: Idea) -> None:
    _state(idea, mood={"悲しみ": 0.6}, level=0.1, at=NOW)
    body = move_feelings.plan(idea, P, NOW)["body"]
    assert sense(body, NOW, P).valence < P.temperament_valence - 0.2
    assert body.slow_arousal < 0  # 悲しみは落ち込む向き


def test_pages_get_an_affect_and_keep_her_words_and_eight_axes(idea: Idea) -> None:
    joyful = _page(idea, "ep-a", {"joy": 0.9, "surprise": 0.6})
    sad = _page(idea, "ep-b", {"sadness": 0.8})
    calm = _page(idea, "ep-c", {})
    before = {p.id: p.path_in(idea.memory).read_text(encoding="utf-8") for p in (joyful, sad, calm)}

    move_feelings.apply(idea, move_feelings.plan(idea, P, NOW))

    pages = {p.id: p for p in load_pages(idea.memory)}
    assert pages["ep-a"].affect["valence"] > 0.5 and pages["ep-b"].affect["valence"] < -0.5
    assert pages["ep-a"].arousal > pages["ep-b"].arousal  # 前の心の動きの強さ（8軸の平均）の順のまま
    assert pages["ep-c"].affect is None  # 気持ちがなければ付けない
    for pid, text in before.items():
        after = pages[pid].path_in(idea.memory).read_text(encoding="utf-8")
        added = [line for line in after.splitlines() if line not in text.splitlines()]
        assert added == ([] if pid == "ep-c" else [added[0]]) and all(line.startswith("affect = ") for line in added)
        assert pages[pid].feeling == (joyful, sad, calm)[["ep-a", "ep-b", "ep-c"].index(pid)].feeling


def test_a_page_that_does_not_round_trip_stops_everything(idea: Idea) -> None:
    page = _page(idea, "ep-a", {"joy": 0.5})
    path = page.path_in(idea.memory)
    path.write_text(path.read_text(encoding="utf-8").replace("+++\n", "+++\n# 手で書いたメモ\n", 1), encoding="utf-8")
    _state(idea, mood={"喜び": 0.5}, level=0.5, at=NOW)
    moving = move_feelings.plan(idea, P, NOW)
    assert moving["not_round_trip"] == ["ep-a"]
    with pytest.raises(RuntimeError):
        move_feelings.apply(idea, moving)
    assert next(FeelingLog(idea.feeling).rows(), None) is None and (idea.data / "emotion_state.json").exists()


def test_without_old_state_it_starts_from_the_usual(idea: Idea) -> None:
    moving = move_feelings.plan(idea, P, NOW)
    assert moving["body"].connection == P.initial_connection and moving["at"] == NOW
    assert moving["state_files"] == []


def test_page_arousal_keeps_the_order_of_the_old_strength(idea: Idea) -> None:
    """記憶の強さの順位は、前の心の動きの強さ（8軸の平均）の順を保つ（記憶テストで決めた順を変えない）。"""
    feelings = [{"trust": 0.9, "joy": 0.8}, {"sadness": 0.9}, {"surprise": 0.3}, {"anger": 0.2, "fear": 0.2}]
    for n, f in enumerate(feelings):
        _page(idea, f"ep-{n}", f)
    move_feelings.apply(idea, move_feelings.plan(idea, P, NOW))
    pages = {p.id: p for p in load_pages(idea.memory)}
    old = sorted(range(4), key=lambda n: sum(feelings[n].values()))
    new = sorted(range(4), key=lambda n: pages[f"ep-{n}"].arousal)
    assert old == new
