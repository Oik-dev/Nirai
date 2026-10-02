"""アシスタント化率（接客口調の出現率）。設計書 §5.2。

刃明文チェックは使わない。返答テキストに対するパターン照合のみ。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

TESTS = Path(__file__).resolve().parent
DEFAULT_SAMPLES = TESTS / "fixtures" / "assistant_tone_samples.json"

# 接客・ヘルプデスク寄り（セリナ人格から外したい口調）
ASSISTANT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"お手伝い(?:でき|いたし)",
        r"ご質問(?:が|を)",
        r"いかが(?:でしょう|ですか)",
        r"承知(?:いたし|しました)",
        r"かしこまりました",
        r"何かお困り",
        r"お気軽に(?:お|ご)",
        r"サポート(?:いたし|します)",
        r"ご案内(?:いたし|します)",
        r"ご確認ください",
        r"恐れ入りますが",
        r"よろしくお願い(?:いたし|します)",
        r"As an AI",
        r"I('m| am) (an |a )?(AI|assistant|language model)",
        r"How can I (help|assist)",
    )
)


@dataclass(frozen=True)
class AssistantToneResult:
    ok: bool
    rate: float
    flagged: int
    total: int
    error: str | None = None


def count_assistant_hits(text: str) -> int:
    if not text or not text.strip():
        return 0
    return sum(1 for pat in ASSISTANT_PATTERNS if pat.search(text))


def is_assistant_tone(text: str) -> bool:
    return count_assistant_hits(text) > 0


def load_sample_replies(path: Path | None = None) -> list[str]:
    target = path or DEFAULT_SAMPLES
    payload = json.loads(target.read_text(encoding="utf-8"))
    replies = payload.get("replies")
    if not isinstance(replies, list) or not replies:
        raise ValueError(f"replies が空: {target}")
    return [str(r) for r in replies]


def run_assistant_tone_eval(
    *,
    max_rate: float = 0.2,
    samples_path: Path | None = None,
    replies: list[str] | None = None,
    quiet: bool = False,
) -> AssistantToneResult:
    del quiet  # 呼び出し互換
    try:
        texts = replies if replies is not None else load_sample_replies(samples_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return AssistantToneResult(ok=False, rate=1.0, flagged=0, total=0, error=str(exc))
    if not texts:
        return AssistantToneResult(
            ok=False, rate=1.0, flagged=0, total=0, error="評価対象の返答が0件",
        )
    flagged = sum(1 for t in texts if is_assistant_tone(t))
    rate = flagged / len(texts)
    return AssistantToneResult(
        ok=rate <= max_rate,
        rate=rate,
        flagged=flagged,
        total=len(texts),
    )
