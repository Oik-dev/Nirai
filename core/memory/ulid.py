"""ULID 生成（純 Python）。合意台帳 §4-3 / Wave 2。

Crockford Base32・26文字・時刻ソート可能。外部ライブラリ禁止。
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(*, now_ms: int | None = None) -> str:
    """新しい ULID を生成する（26文字・Crockford Base32）。"""
    timestamp_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if timestamp_ms < 0 or timestamp_ms >= 1 << 48:
        raise ValueError("timestamp_ms は 48 bit 範囲内である必要がある")

    time_part = _encode_base32(timestamp_ms, 10)
    random_bytes = os.urandom(10)
    random_value = int.from_bytes(random_bytes, "big")
    random_part = _encode_base32(random_value, 16)
    return time_part + random_part
