"""設定ファイル（ツマミ）の読み込みテスト。設計書 §2.5(確信度足切り), §2.3(急変防止弁), §5.5-3"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import ThresholdsConfig, load_thresholds


def test_load_thresholds_from_default_file() -> None:
    cfg = load_thresholds()
    assert isinstance(cfg, ThresholdsConfig)
    assert cfg.fusen_confidence["心の動き"] > 0
    assert cfg.mood_guard_max_delta_per_turn > 0


def test_confidence_threshold_for_unknown_kind_falls_back_to_default() -> None:
    cfg = load_thresholds()
    assert cfg.confidence_threshold_for("未登録の種類") == cfg.default_confidence_threshold


def test_context_window_and_timeouts_are_configured() -> None:
    """§1.4 / §5.5-3: 直近会話窓とHTTPタイムアウトもツマミ"""
    cfg = load_thresholds()
    assert cfg.recent_turns_small > 0
    assert cfg.recent_turns_large >= cfg.recent_turns_small
    assert cfg.recent_turns_for("small") == cfg.recent_turns_small
    assert cfg.recent_turns_for("large") == cfg.recent_turns_large
    assert cfg.fine_band_turns > 0
    assert cfg.coarse_update_every_n_turns > 0
    assert cfg.qwen_request_timeout_seconds > 0
    assert cfg.embedder_request_timeout_seconds > 0


def test_memory_dedup_and_job_cap_are_configured() -> None:
    """§4.3: dedup閾値はツマミ。§2.5: 1蒸留ジョブあたりの記憶化件数に上限"""
    cfg = load_thresholds()
    assert 0.0 < cfg.memory_dedup_threshold <= 1.0
    assert cfg.memory_max_candidates_per_job > 0


def test_emotion_render_thresholds_are_configured() -> None:
    """§1.5段⑤・§2.3: 感情状態の意訳閾値もツマミ（ハードコード禁止）"""
    cfg = load_thresholds()
    assert 0.0 <= cfg.emotion_ignore_below < cfg.emotion_mild_below < cfg.emotion_strong_below <= 1.0
    assert cfg.emotion_affect_top_n >= 1


def test_emotion_decay_and_baseline_are_configured() -> None:
    """§2.3: 時間冷却と baseline もツマミ"""
    cfg = load_thresholds()
    assert cfg.tau_affect_seconds > 0
    assert cfg.tau_mood_seconds > cfg.tau_affect_seconds
    assert 0.0 <= cfg.emotion_dyad_min <= 1.0
    assert cfg.emotion_baselines is not None
    assert cfg.emotion_baselines.get("喜び", 0.0) > 0


def test_pulse_and_persona_blade_thresholds_are_configured() -> None:
    """§2.8 Pulse / §2.9 見えるブレーキ"""
    cfg = load_thresholds()
    assert cfg.pulse_idle_before_seconds >= 60
    assert 0 <= cfg.pulse_active_hour_start < 24
    assert cfg.persona_blade_visible_brake_mode in ("none", "parenthetical", "suffix")
    pc = cfg.pulse_config()
    assert pc.same_kind_gap_seconds >= pc.min_interval_seconds


def main() -> None:
    tests = [
        test_load_thresholds_from_default_file,
        test_confidence_threshold_for_unknown_kind_falls_back_to_default,
        test_context_window_and_timeouts_are_configured,
        test_memory_dedup_and_job_cap_are_configured,
        test_emotion_render_thresholds_are_configured,
        test_emotion_decay_and_baseline_are_configured,
        test_pulse_and_persona_blade_thresholds_are_configured,
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
