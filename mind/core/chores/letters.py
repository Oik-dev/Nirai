"""友達との手紙の事実。正本は郵便局と精神の追記専用生ログ。

外部への送信とモデル呼び出しはここではしない。送信結果不明も、
送ろうとした letter-time の行を根拠に重複を防ぐ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
import json


@dataclass(frozen=True)
class LogLine:
    path: Path
    number: int
    value: dict

    @property
    def source(self) -> str:
        return f"{self.path}#{self.number}"


def read_lines(directory: Path) -> list[LogLine]:
    """JSONLを行番号付きで読む。破損行を黙って飛ばすと再送の危険がある。"""
    if not directory.exists():
        return []
    lines = []
    for path in sorted(directory.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as file:
            for number, raw in enumerate(file, 1):
                if raw.strip():
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError(f"手紙の記録が辞書でない: {path}#{number}")
                    lines.append(LogLine(path, number, value))
    # postedにはtsがなくtimeがある。ファイル・行で読んだ順を維持する。
    return lines


def append_line(directory: Path, value: dict) -> None:
    ts = datetime.fromisoformat(value.get("ts") or value["time"])
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{ts.year:04d}-{ts.month:02d}.jsonl"
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def pending_letters(post: Iterable[LogLine], times: Iterable[LogLine], *, resident: str = "Serina") -> tuple[list[LogLine], list[LogLine]]:
    """(まだ読んでいないもの, 読んだが郵便局の済印だけ未完のもの)。"""
    post = list(post)
    times = list(times)
    delivered = {r.value.get("id"): r for r in post if r.value.get("kind") == "letter" and r.value.get("to") == resident}
    done = {r.value.get("letter") for r in post if r.value.get("kind") == "done"}
    read = {letter for r in times if r.value.get("kind") == "letter-time" for letter in r.value.get("read", [])}
    waiting = [r for id_, r in delivered.items() if id_ not in done]
    waiting.sort(key=lambda r: r.value.get("ts", ""))
    return ([r for r in waiting if r.value["id"] not in read], [r for r in waiting if r.value["id"] in read])


def needs_letter_time(*, activity: str | None, last_time: datetime | None, selected_at: datetime | None, unread_count: int) -> bool:
    if activity != "手紙を書く":
        return False
    if unread_count:
        return True
    return selected_at is not None and (last_time is None or selected_at > last_time)


def last_letter_time(lines: Iterable[LogLine]) -> datetime | None:
    timestamps = [datetime.fromisoformat(r.value["ts"]) for r in lines if r.value.get("kind") == "letter-time"]
    return max(timestamps, default=None)


def can_send_again(
    friend: str,
    now: datetime,
    post: Iterable[LogLine],
    times: Iterable[LogLine],
    *,
    day_of: Callable[[datetime], str],
) -> bool:
    """同じSerina日には返事が来るまで重ねない。postedがなくても送信試行を数える。"""
    entries = list(times)
    failed = {r.value.get("time") for r in entries if r.value.get("kind") == "posted" and "failed" in r.value}
    writes = [r.value for r in entries if r.value.get("kind") == "letter-time"
              and (r.value.get("write") or {}).get("to") == friend
              and r.value.get("kept") is None
              and r.value.get("ts") not in failed
              and day_of(datetime.fromisoformat(r.value["ts"])) == day_of(now)]
    if not writes:
        return True
    newest = max(datetime.fromisoformat(r["ts"]) for r in writes)
    return any(r.value.get("kind") == "letter" and r.value.get("from") == friend
               and datetime.fromisoformat(r.value["ts"]) > newest for r in post)


def reply_to(friend: str, read: Iterable[LogLine]) -> str | None:
    received = [r for r in read if r.value.get("kind") == "letter" and r.value.get("from") == friend]
    return max(received, key=lambda r: r.value["ts"]).value["id"] if received else None


def current_activity(body: Iterable[LogLine]) -> str | None:
    """海で最後に選ばれた活動。精神は同じ状態を持たない。"""
    choices = [row.value for row in body if row.value.get("kind") == "activity"]
    return choices[-1].get("value") if choices else None


def today_letters(post: Iterable[LogLine], times: Iterable[LogLine], *, day_of: Callable[[datetime], str], now: datetime) -> dict:
    """文脈の背景用。本文を見せずに未読の到着だけも表示できる。"""
    post = list(post)
    times = list(times)
    day = day_of(now)
    received = [r for r in post if r.value.get("kind") == "letter" and day_of(datetime.fromisoformat(r.value["ts"])) == day]
    written = [r for r in times if r.value.get("kind") == "letter-time" and day_of(datetime.fromisoformat(r.value["ts"])) == day]
    unread, _ = pending_letters(post, times)
    counts: dict[str, int] = {}
    for r in unread:
        friend = r.value.get("from", "誰か")
        counts[friend] = counts.get(friend, 0) + 1
    notice = "、".join(f"{friend}から{count}通" for friend, count in counts.items())
    return {"received": received, "written": written, "unread": unread, "notice": notice}


def home_clear_to_send(brain_location: str, text: str, thought: str, tell: str,
                       marks, judge: Callable[[list[str]], list[int]] | None = None) -> bool:
    """Gemmaの未判定も判定失敗も閉じる。Geminiは外の文として受け付ける。"""
    if brain_location == "cloud":
        return True
    if marks is None or judge is None:
        return False
    unknown = marks.unmarked([text, thought, tell])
    try:
        if unknown:
            marks.mark(unknown, judge(unknown))
        return not marks.home(text)
    except Exception:  # noqa: BLE001 - 誤判定でも必ず送らない
        return False


def letter_context(idea, *, now: datetime, day_of, marks=None, cloud: bool = False,
                   limit: int = 1600) -> str:
    """話す前の背景。本文を知らない未読は差出人と数だけ。"""
    view = today_letters(read_lines(idea.lifelog / "post"), read_lines(idea.lifelog / "letters"),
                         now=now, day_of=day_of)
    safe = marks.safe_only if cloud and marks else lambda s: s
    rows = []
    if view["notice"]:
        rows.append("未読: " + view["notice"])
    for item in view["written"]:
        row = item.value
        if row.get("thought"):
            rows.append("手紙の時間に思ったこと: " + safe(row["thought"]))
        if row.get("write"):
            rows.append("送ろうとした手紙: " + safe(row["write"]["text"]))
    for item in view["received"]:
        if item not in view["unread"]:
            rows.append(f"{item.value.get('from', '友達')}から: " + safe(item.value.get("body", "")))
    return "\n".join(row for row in rows if row.strip())[-limit:]
