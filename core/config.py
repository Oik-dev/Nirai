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
    # 自発想起の足切り（bge-m3は無関係でも0.7前後を出すため、これは明白なゴミ除去用の緩い床）
    memory_min_relevance: float = 0.6
    temperature: float = 0.8
    # Ollamaのデフォルトnum_ctx変動（自動更新で32kに膨張→CPUオフロードで激遅）への防御。
    # KV量子化(q8_0)+Flash Attention 前提で 8192 が 8GB VRAM の最適点
    # （サブ機12GBなら16384まで上げてよい。要 OLLAMA_KV_CACHE_TYPE=q8_0 / OLLAMA_FLASH_ATTENTION=1）
    num_ctx: int = 8192
    distill_num_ctx: int = 8192
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
    # 減衰・想起（3c）
    trigger_block_char_cap: int = 800
    hot_min_access: int = 5
    hot_recent_days: int = 14
    hot_max_items: int = 3
    # 感情重力（3c）
    emotion_tau_days: float = 3.0
    emotion_word_clamp: float = 0.15
    shy_spike: float = 0.25
    shy_threshold: float = 0.95
