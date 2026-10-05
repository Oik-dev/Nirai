"""設定ファイル（ツマミ）の読み込みテスト。設計書 §5.5-3（閾値・幅はすべて設定ファイル化）。

守るもの：気持ちの対応表は、評価の選択肢と同じ顔ぶれでなければ読まない（抜けがあると、気持ちが黙って動かなくなる）。
気持ちのツマミの正本は設定ファイルだけ（コードに既定値を持たない）。
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import DEFAULT_THRESHOLDS_PATH, ThresholdsConfig, load_thresholds
from mind.core.feeling.appraisal import AROUSAL, DISTANCE, VALENCE


def test_load_thresholds_from_default_file() -> None:
    cfg = load_thresholds()
    assert isinstance(cfg, ThresholdsConfig)
    assert cfg.feeling is not None


def test_context_window_and_timeouts_are_configured() -> None:
    """§1.4 / §5.5-3: 直近会話窓とHTTPタイムアウトもツマミ"""
    cfg = load_thresholds()
    assert cfg.recent_turns_small > 0
    assert cfg.recent_turns_large >= cfg.recent_turns_small
    assert cfg.recent_turns_for("small") == cfg.recent_turns_small
    assert cfg.recent_turns_for("large") == cfg.recent_turns_large
    assert cfg.fine_band_turns > 0
    assert cfg.coarse_update_every_n_turns > 0
    assert cfg.ollama_request_timeout_seconds > 0
    assert cfg.embedder_request_timeout_seconds > 0


def test_ollama_section_values_match_raw_toml() -> None:
    """`[ollama]`セクション名とローダーキーの一致をtoml実値と突き合わせて担保する。

    既定値とtoml値が偶然一致していると、セクション名やキー名を間違えても
    raw.get()が黙って既定値へフォールバックしテストが緑のまま通ってしまう
    （2026-07-26 qwen→ollamaリネームのcompletion-review指摘I-2）。
    """
    raw = tomllib.loads(DEFAULT_THRESHOLDS_PATH.read_text(encoding="utf-8"))
    cfg = load_thresholds()
    assert cfg.ollama_num_ctx == raw["ollama"]["num_ctx"]
    assert cfg.ollama_use_mmap == raw["ollama"]["use_mmap"]
    assert cfg.ollama_request_timeout_seconds == raw["ollama"]["request_timeout_seconds"]


def test_feeling_table_covers_every_choice_and_points_the_right_way() -> None:
    """§2.3: 評価の選択肢ごとの動きは対応表にあり、向きが選択肢の意味と合っている。"""
    f = load_thresholds().feeling
    assert tuple(f.valence) == VALENCE and tuple(f.arousal) == AROUSAL and tuple(f.distance) == DISTANCE
    assert [f.valence[c] for c in VALENCE] == sorted(f.valence.values())  # 嫌 → うれしい の順に上がる
    assert [f.arousal[c] for c in AROUSAL] == sorted(f.arousal.values())
    assert f.distance["近づいた"] > f.distance["変わらない"] > 0 > f.distance["離れた"]  # 話したこと自体で少し満ちる
    assert all(-1.0 <= k <= 1.0 for k in (*f.valence.values(), *f.arousal.values(), *f.distance.values()))
    assert f.tau_fast_seconds < f.tau_slow_seconds
    assert 0 < f.slow_share < 1 and 0 < f.lonely_below < 1


def test_feeling_table_with_a_missing_choice_is_refused(tmp_path: Path) -> None:
    text = DEFAULT_THRESHOLDS_PATH.read_text(encoding="utf-8").replace('"大きく動いた" = 0.5\n', "")
    broken = tmp_path / "thresholds.toml"
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="feeling.arousal"):
        load_thresholds(broken)


def test_pulse_and_persona_blade_thresholds_are_configured() -> None:
    """§2.8 Pulse / §2.9 見えるブレーキ"""
    cfg = load_thresholds()
    assert 0 <= cfg.pulse_active_hour_start < 24
    assert cfg.persona_blade_visible_brake_mode in ("none", "parenthetical", "suffix")
    pc = cfg.pulse_config()
    assert pc.same_kind_gap_seconds >= pc.min_interval_seconds
