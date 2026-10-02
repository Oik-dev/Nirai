"""Core intake: 関所①②③＋状態更新（§2.5, §2.7）。Phase1は④引用照合・記憶審査を含まない"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import ThresholdsConfig
from mind.core.intake.gate import process_report
from mind.core.state.desire import DesireState
from mind.core.state.emotion import EmotionState
from mind.core.state.relationship import RelationshipState


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5, "心の動き": 0.5, "マスター観測": 0.5},
        mood_guard_max_delta_per_turn=0.1,
    )


def _raw_report(fusen_list: list[dict]) -> dict:
    return {
        "reply": "そうだったんですね",
        "fusen_list": fusen_list,
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    }


def test_accepted_fusen_updates_affect_and_bleeds_mood() -> None:
    """2026-07-26 A6: 気分は情動へ直接加算されず、にじみ（apply_mood_bleed）で1ターン
    分だけ引き寄せられる。mood_guard_max_delta_per_turn=0.1は安全上限であり、
    bleed_rate（既定0.08）×情動と気分の差が主機構。"""
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.6}, "trigger": "褒められた"},
            "confidence": 0.9,
        }
    ])

    thresholds = _thresholds()
    result = process_report(raw, emotion=emotion, relationship=relationship, thresholds=thresholds)

    assert emotion.affect["喜び"] == 0.6
    expected_mood = round((0.6 - 0.0) * thresholds.emotion_mood_bleed_rate, 10)
    assert round(emotion.mood["喜び"], 10) == expected_mood
    assert len(result.accepted_fusen) == 1
    assert result.rejected_by_confidence == []
    assert result.discarded_by_format == []


def test_low_confidence_fusen_is_rejected_and_state_untouched() -> None:
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"怒り": 0.5}, "trigger": "些細な違和感"},
            "confidence": 0.2,  # 閾値0.5未満
        }
    ])

    result = process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert emotion.affect["怒り"] == 0.0
    # 2026-07-26 A6: 棄却された付箋は情動にも気分にも影響しない
    # （mood_bleedはループ完了後に走るが、affectが動いていないので気分も動かない）。
    assert emotion.mood["怒り"] == 0.0
    assert len(result.rejected_by_confidence) == 1
    assert result.accepted_fusen == []


def test_master_observation_updates_relationship() -> None:
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "マスター観測",
            "version": 1,
            "content": {"observation": "疲れてそう"},
            "confidence": 0.8,
        }
    ])

    process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert relationship.recent_master_mood == "疲れてそう"


def test_broken_fusen_is_discarded_and_reported() -> None:
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "マスター観測",
            "version": 1,
            "content": {"observation": "機嫌が良い"},
            "confidence": 3.0,  # 書式違反(範囲外)
        }
    ])

    result = process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert len(result.discarded_by_format) == 1
    assert relationship.recent_master_mood is None


def test_mood_bleeds_toward_affect_even_with_zero_fusen() -> None:
    """2026-07-26 A6: 付箋が1枚も無いターンでも、既存の情動へ気分が寄る
    （mood_bleedは毎ターン1回呼ばれる契約）。"""
    emotion = EmotionState()
    emotion.affect["喜び"] = 0.5  # 前ターンまでに蓄積済みの情動という体
    relationship = RelationshipState()
    raw = _raw_report([])  # 付箋0枚

    process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert emotion.mood["喜び"] > 0.0


def test_desire_fulfillment_discharges_on_big_joy_delta() -> None:
    """Task 3-4: 欲求高＋門開＋喜びdelta大 → 放電・不応・喜び/信頼ブースト。"""
    emotion = EmotionState()
    desire = DesireState()
    desire.level = 0.7
    desire.level_before_tick = 0.7  # tick前スナップショット（2026-07-30 C-2是正）
    relationship = RelationshipState()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.35}, "trigger": "触れ合い"},
            "confidence": 0.9,
        }
    ])
    thresholds = _thresholds()
    joy_before_boost_path = 0.35  # fusen delta 適用後、ブースト前の見込み
    process_report(
        raw,
        emotion=emotion,
        relationship=relationship,
        thresholds=thresholds,
        desire=desire,
        now=now,
    )
    assert desire.level == thresholds.desire_discharge_level
    assert desire.refractory_until is not None
    assert desire.is_in_refractory(now)
    # fusen delta + fulfillment_boost（対極カップリングあり、上限1.0）
    assert emotion.affect["喜び"] >= joy_before_boost_path + thresholds.desire_fulfillment_boost - 1e-9
    assert emotion.affect["信頼"] >= thresholds.desire_fulfillment_boost - 1e-9


def test_desire_fulfillment_uses_level_before_tick_not_current_level() -> None:
    """2026-07-30 レビューC-2是正bの再現テスト: levelが閾値未満でもlevel_before_tick
    が閾値以上なら放電する（is_fulfillment_level_high()の現在値に戻すと壊れる）。
    """
    emotion = EmotionState()
    desire = DesireState()
    desire.level = 0.55  # 閾値(0.6)未満＝tickの未充足減衰で僅かに下がった後の値
    desire.level_before_tick = 0.65  # tick前は閾値以上だった
    relationship = RelationshipState()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.35}, "trigger": "触れ合い"},
            "confidence": 0.9,
        }
    ])
    process_report(
        raw,
        emotion=emotion,
        relationship=relationship,
        thresholds=_thresholds(),
        desire=desire,
        now=now,
    )
    assert desire.level == _thresholds().desire_discharge_level
    assert desire.refractory_until is not None


def test_desire_fulfillment_does_not_discharge_twice_in_same_turn() -> None:
    """2026-07-30 レビューC-b再発防止: 同ターンに「心の動き」付箋が複数あっても
    2枚目以降で再放電しない（discharge_and_enter_refractoryのlevel_before_tick
    リセット＋不応期ガードの多重防御）。
    """
    emotion = EmotionState()
    desire = DesireState()
    desire.level = 0.7
    desire.level_before_tick = 0.7
    relationship = RelationshipState()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    thresholds = _thresholds()
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.35}, "trigger": "触れ合い"},
            "confidence": 0.9,
        },
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.35}, "trigger": "触れ合い（続き）"},
            "confidence": 0.9,
        },
    ])
    process_report(
        raw,
        emotion=emotion,
        relationship=relationship,
        thresholds=thresholds,
        desire=desire,
        now=now,
    )
    assert desire.level == thresholds.desire_discharge_level
    # ブーストは1回だけ（2回適用ならtrustは0.6超になる）
    assert emotion.affect["信頼"] <= thresholds.desire_fulfillment_boost + 1e-9


def test_desire_fulfillment_skipped_when_level_low() -> None:
    """Task 3-4: level が閾値未満なら放電しない。"""
    emotion = EmotionState()
    desire = DesireState()
    desire.level = 0.4
    desire.level_before_tick = 0.4  # tick前スナップショット（2026-07-30 C-2是正）
    relationship = RelationshipState()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.4}, "trigger": "軽い好意"},
            "confidence": 0.9,
        }
    ])
    process_report(
        raw,
        emotion=emotion,
        relationship=relationship,
        thresholds=_thresholds(),
        desire=desire,
        now=now,
    )
    assert desire.level == 0.4
    assert desire.refractory_until is None


def test_desire_fulfillment_skipped_when_gate_closed() -> None:
    """Task 3-4: 抑制門が閉じていると放電しない。"""
    emotion = EmotionState()
    emotion.affect["嫌悪"] = 0.7  # 門を閉じる
    desire = DesireState()
    desire.level = 0.8
    desire.level_before_tick = 0.8  # tick前スナップショット（2026-07-30 C-2是正）
    relationship = RelationshipState()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.4}, "trigger": "触れ合い"},
            "confidence": 0.9,
        }
    ])
    process_report(
        raw,
        emotion=emotion,
        relationship=relationship,
        thresholds=_thresholds(),
        desire=desire,
        now=now,
    )
    assert desire.level == 0.8
    assert desire.refractory_until is None


def test_desire_fulfillment_on_trust_delta_only() -> None:
    """Task 3-4: 信頼 delta だけでも放電する（喜びは閾値未満）。"""
    emotion = EmotionState()
    desire = DesireState()
    desire.level = 0.7
    desire.level_before_tick = 0.7  # tick前スナップショット（2026-07-30 C-2是正）
    relationship = RelationshipState()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    thresholds = _thresholds()
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"信頼": 0.31, "喜び": 0.1}, "trigger": "安心"},
            "confidence": 0.9,
        }
    ])
    process_report(
        raw,
        emotion=emotion,
        relationship=relationship,
        thresholds=thresholds,
        desire=desire,
        now=now,
    )
    assert desire.level == thresholds.desire_discharge_level
    assert desire.is_in_refractory(now)


def test_desire_fulfillment_skipped_when_delta_below_threshold() -> None:
    """Task 3-4: 喜び/信頼 delta が 0.3 未満なら放電しない。"""
    emotion = EmotionState()
    desire = DesireState()
    desire.level = 0.8
    desire.level_before_tick = 0.8  # tick前スナップショット（2026-07-30 C-2是正）
    relationship = RelationshipState()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.29, "信頼": 0.29}, "trigger": "弱い反応"},
            "confidence": 0.9,
        }
    ])
    process_report(
        raw,
        emotion=emotion,
        relationship=relationship,
        thresholds=_thresholds(),
        desire=desire,
        now=now,
    )
    assert desire.level == 0.8
    assert desire.refractory_until is None


def main() -> None:
    tests = [
        test_accepted_fusen_updates_affect_and_bleeds_mood,
        test_low_confidence_fusen_is_rejected_and_state_untouched,
        test_master_observation_updates_relationship,
        test_broken_fusen_is_discarded_and_reported,
        test_desire_fulfillment_discharges_on_big_joy_delta,
        test_desire_fulfillment_skipped_when_level_low,
        test_desire_fulfillment_skipped_when_gate_closed,
        test_desire_fulfillment_on_trust_delta_only,
        test_desire_fulfillment_skipped_when_delta_below_threshold,
    ]
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
