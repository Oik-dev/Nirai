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
