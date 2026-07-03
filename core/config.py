"""Core 設定値"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CoreConfig:
    model: str = "hf.co/Aratako/NemoAurora-RP-12B-GGUF:IQ4_XS"
    base_url: str = "http://127.0.0.1:11434"
    memory_k: int = 6
    history_n: int = 24
    memory_block_char_cap: int = 1500
    pinned_block_char_cap: int = 8000
    temperature: float = 0.8
    # セッション/アーカイブ（3a）
    session_timeout_hours: float = 6.0
    living_date_offset_hours: int = 4
    archive_retention_days: int = 30
    # 蒸留/リフレクション（3b）
    reason_model: str = "hf.co/mradermacher/gemma-4-12B-it-uncensored-heretic-i1-GGUF:IQ4_XS"
    reason_temperature: float = 0.2
    distill_input_char_cap: int = 12000
    distill_map_summary_char_cap: int = 2000
    open_thread_ttl_days: int = 14
    open_thread_max_inject: int = 2
    open_thread_inject_history_max: int = 6
    fact_match_max_candidates: int = 20
    diary_importance: float = 0.55
    fact_importance: float = 0.7
    # 感情ベースライン（3cで重力に使用。3bでは初期値として参照）
    baseline_intimacy: float = 0.4
    baseline_tension: float = 0.3
    baseline_energy: float = 0.5
