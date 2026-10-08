"""app/server._produce_turn のイベント順テスト（2026-07-20 応答高速化）。

期待順序: token* → done(reply・1通目確定) → done(reply+citations・終幕)。
返事が決まったら（on_reply）その場で会話を記録し、said と、本人が選んだ体（body）をその返事の行の ref で流す。
その場所を添えて気持ちを残す（Core.feel）。終幕の done はそのあと。
記録に書けなかったら、書けたように扱わない（notice で知らせ、気持ちは動かさない）。
Ollama 不要（フェイク Core のみ）。

2026-07-31 Phase E: 旧「保留文→2通目」機構（followupイベント）は退役済み。
出典（citations）は終幕の done イベントへ付加フィールドとして届く
（reply・会話の記録には混ざらない契約。Phase D-6）。
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.app import server
from mind.core.lifelog import MASTER
from mind.core.perception import BodyChoice


class _FakeLog:
    """会話の記録の替え玉。書いた順に (話し手, 本文) を持ち、その日の行番号を返す。"""

    def __init__(self, *, broken: bool = False) -> None:
        self.lines: list[tuple[str, str]] = []
        self.broken = broken

    def append(self, *, ts: str, speaker: str, text: str) -> tuple[str, int]:  # noqa: ARG002
        if self.broken:
            raise OSError("ディスクがいっぱい")
        self.lines.append((speaker, text))
        return ("2026-10-06", len(self.lines))


class _FakeCore:
    """turn_routed の callbacks 契約だけを再現する。"""

    def __init__(
        self,
        reply: str,
        citations: list[dict] | None = None,
        raise_after_reply: bool = False,
        body: BodyChoice | None = None,
    ) -> None:
        self._reply = reply
        self._citations = citations
        self._raise_after_reply = raise_after_reply
        self._body = body
        self.felt: list[dict] = []
        self.log: _FakeLog | None = None

    def turn_routed(self, text: str, *, now, on_token=None, on_reply=None, on_body=None):  # noqa: ANN001, ANN201
        for ch in self._reply:
            if on_token is not None:
                on_token(ch)
        if on_reply is not None:
            on_reply(self._reply)
        if self._body is not None and on_body is not None:
            on_body(self._body)  # 評価の先頭の体の欄（評価の全体より先に届く）
        if self._raise_after_reply:
            raise RuntimeError("抽出段の失敗")
        return SimpleNamespace(
            report=SimpleNamespace(reply=self._reply),
            citations=self._citations,
        )

    def feel(self, result, *, source, now):  # noqa: ANN001, ANN201
        self.felt.append({"reply": result.report.reply, "source": tuple(source), "lines": list(self.log.lines)})


def _state(core: _FakeCore, log: _FakeLog) -> server.MindState:
    state = server.MindState.__new__(server.MindState)
    core.log = log
    state.core = core
    state.conversation = log
    state.name = "Serina"
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    state.last_activity_at = datetime.now(timezone.utc)
    state.call_fn = lambda _prompt: ""
    server.STATE = state
    return state


def _run_turn(text: str, core: _FakeCore, log: _FakeLog | None = None) -> tuple[list[dict], _FakeLog]:
    log = log or _FakeLog()
    _state(core, log)
    events: "queue.Queue[str | None]" = queue.Queue()
    server._produce_turn(text, events)
    out: list[dict] = []
    while (item := events.get_nowait()) is not None:
        out.append(json.loads(item))
    return out, log


def test_event_order_token_done_citations_finaldone() -> None:
    """統合パイプライン（Phase D/E）: 1ターン=1通のみ。citationsは終幕doneの付加フィールド。"""
    citations = [{"url": "https://example.com/weather"}]
    events, log = _run_turn("天気教えて", _FakeCore("晴れだよ", citations=citations))

    types = [e["type"] for e in events]
    assert types == ["token"] * 4 + ["done", "done"], "followupイベントは退役済み（1通完結）"
    assert "".join(e["text"] for e in events[:4]) == "晴れだよ"
    first_done, final_done = events[4], events[5]
    assert first_done == {"type": "done", "reply": "晴れだよ"}
    assert final_done == {"type": "done", "reply": "晴れだよ", "citations": citations}  # 帳簿の session_id はもうない
    assert log.lines == [(MASTER, "天気教えて"), ("Serina", "晴れだよ")], "citationsは会話の記録に混入しない"


def test_no_citations_emits_null_citations_field() -> None:
    events, log = _run_turn("こんにちは", _FakeCore("やあ"))

    types = [e["type"] for e in events]
    assert types == ["token"] * 2 + ["done", "done"]
    assert events[-1]["citations"] is None
    assert log.lines == [(MASTER, "こんにちは"), ("Serina", "やあ")]


def test_normal_reply_publishes_said_after_record(monkeypatch) -> None:  # noqa: ANN001
    published: list[dict] = []
    monkeypatch.setattr(server, "_publish_event", lambda _state, event: published.append(event))

    _run_turn("ただいま", _FakeCore("おかえり"))

    assert published == [{
        "type": "said",
        "ref": "lifelog/conversation/2026-10-06.jsonl#2-2",
        "text": "おかえり",
    }]


def test_failure_after_reply_delivered_emits_notice_not_error() -> None:
    """1通目が届いた後の裏方失敗で、表示済み本文を謝り文言で上書きしない。返事は届いた時点で記録に残っている。"""
    events, log = _run_turn("こんにちは", _FakeCore("やあ", raise_after_reply=True))

    types = [e["type"] for e in events]
    assert types[-1] == "notice"
    assert "error" not in types
    assert log.lines == [(MASTER, "こんにちは"), ("Serina", "やあ")]


def test_the_body_choice_flows_with_the_ref_of_the_recorded_reply(monkeypatch) -> None:  # noqa: ANN001
    """本人が選んだ体は、評価の全体を待たずに、記録に残った返事の行を ref にして流れる（「そのまま」の欄は来ない）。"""
    published: list[dict] = []
    monkeypatch.setattr(server, "_publish_event", lambda _state, event: published.append(event))

    _run_turn("ただいま", _FakeCore("おかえり", body=BodyChoice(expression="喜び")))

    ref = "lifelog/conversation/2026-10-06.jsonl#2-2"
    assert published == [
        {"type": "said", "ref": ref, "text": "おかえり"},
        {"type": "body", "by": "reply", "ref": ref, "expression": "喜び"},
    ]


def test_no_body_flows_for_a_reply_that_could_not_be_recorded(monkeypatch) -> None:  # noqa: ANN001
    published: list[dict] = []
    monkeypatch.setattr(server, "_publish_event", lambda _state, event: published.append(event))

    _run_turn("ただいま", _FakeCore("おかえり", body=BodyChoice(gesture="うなずく")), _FakeLog(broken=True))

    assert published == []


def test_feelings_are_left_after_the_turn_is_recorded() -> None:
    """気持ちの記録は、拠った会話の場所（発言と返事）を持つ。だから会話を記録してから残す。"""
    core = _FakeCore("おかえり")
    _run_turn("ただいま", core)
    assert core.felt == [{
        "reply": "おかえり",
        "source": ("lifelog/conversation/2026-10-06.jsonl#1-2",),
        "lines": [(MASTER, "ただいま"), ("Serina", "おかえり")],
    }]


def test_a_turn_that_cannot_be_recorded_says_so_and_moves_no_feelings(monkeypatch) -> None:  # noqa: ANN001
    published: list[dict] = []
    monkeypatch.setattr(server, "_publish_event", lambda _state, event: published.append(event))
    core = _FakeCore("おかえり")

    events, _ = _run_turn("ただいま", core, _FakeLog(broken=True))

    assert [e["type"] for e in events][-2:] == ["done", "notice"]  # 返事は届いたが、終幕の done は出さない
    assert events[-1]["text"] == server.UNRECORDED_NOTICE
    assert core.felt == [] and published == []


def test_closing_the_http_stream_does_not_stop_the_turn_from_being_recorded() -> None:
    """窓が返事の途中で閉じても、製造スレッドは最後まで走って会話を記録する。"""
    release = threading.Event()

    class SlowCore(_FakeCore):
        def turn_routed(self, text: str, *, now, on_token=None, on_reply=None, on_body=None):  # noqa: ANN001, ANN201
            if on_token is not None:
                on_token("返")
            release.wait(timeout=2)
            if on_reply is not None:
                on_reply(self._reply)
            return SimpleNamespace(report=SimpleNamespace(reply=self._reply), citations=None)

    log = _FakeLog()
    _state(SlowCore("返事"), log)

    stream = server._chat_events("途中で閉じる")
    first = json.loads(next(stream))
    assert first == {"type": "token", "text": "返"}
    stream.close()
    release.set()
    for _ in range(100):
        if len(log.lines) == 2:
            break
        time.sleep(0.01)
    assert log.lines == [(MASTER, "途中で閉じる"), ("Serina", "返事")]
