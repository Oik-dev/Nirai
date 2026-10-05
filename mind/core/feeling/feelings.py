"""気持ち（設計書 §2.3）。体の芯と、本人の言葉の気持ちの流れ。正本は気持ちの記録（lifelog/feeling/。core/lifelog.py）。

- Masterが話したターンのたびに、本人の脳が返答のあとに答えた評価（appraisal.py）で体の芯を動かし（body.py）、
  言葉と評価と動いたあとの数を1行に残す（feel）。評価を聞けなかったターンも、Masterが来たことは残る（つながりが少し満ちる）。
  本人が自分から話しかけた（Pulse）だけでは、つながりは満ちない。
- 今の気持ちは、最後の行の数から、それからの時間で落ち着かせて計算する。状態のファイルを持たない。
- 文脈パックの⑤（for_pack）：直近の本人の言葉（いつのことか付き）・体の感じ（素朴な言葉。感情の名前は付けない）・
  マスターの様子（いつの見立てか付き。古い見立ては載せない）。数は載せない。
- 眠り：出来事のあいだの気持ちから、ページの芯の数をピーク・エンドで決める（peak_end）。日記の材料は、その日の気持ちの流れ（flow_lines）。
- Pulse：つながりが減って人恋しいとき（lonely）、本人から会いに行く。
- Masterが会話を消したら、その会話に拠った行の言葉を消す（forget）。数は残る。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice

from mind.core.feeling.appraisal import MET, Appraisal
from mind.core.feeling.body import JST, Body, FeelingParams, Sense, is_night, react, sense, settle
from mind.core.lifelog import FeelingLog, Position, positions_of

SCAN_ROWS = 200  # パックの材料（直近の言葉・マスターの様子）を探すのに読む、新しいほうからの行の数


@dataclass(frozen=True)
class Felt:
    """気持ちの記録の1行を、そのときの感じとして読んだもの。"""

    at: datetime
    words: str  # 本人の言葉（なければ空）
    master_state: str
    sense: Sense
    source: tuple[str, ...]


def _at(row: dict) -> datetime:
    return datetime.fromisoformat(row["ts"])


def ago(seconds: float) -> str:
    """気持ちの「いつのことか」。会話の中の時間なので、分と時間の細かさで言う。"""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "たった今"
    if seconds < 3600:
        return f"{int(seconds // 60)}分前"
    if seconds < 86400:
        return f"{int(seconds // 3600)}時間前"
    return f"{int(seconds // 86400)}日前"


class Feelings:
    def __init__(self, log: FeelingLog, params: FeelingParams) -> None:
        self.log = log
        self.params = params

    # --- 今の気持ち ----------------------------------------------------------------

    def _last(self) -> dict | None:
        return next(iter(self.log.newest_first()), None)

    def body(self, at: datetime) -> Body:
        """at の体の芯（最後の記録から、それからの時間だけ落ち着かせる）。記録がなければ平常で、つながりは初めの値。"""
        last = self._last()
        if last is None:
            return Body(connection=self.params.initial_connection)
        return settle(Body.from_record(last["after"]), (at - _at(last)).total_seconds(), self.params)

    def sense(self, at: datetime) -> Sense:
        return sense(self.body(at), at, self.params)

    def lonely(self, at: datetime) -> bool:
        return self.sense(at).connection < self.params.lonely_below

    def body_words(self, at: datetime) -> str:
        """体の感じを、ごく素朴な言葉で（感情の名前は付けない。名前を付けるのは本人の脳）。"""
        p, s = self.params, self.sense(at)
        words = []
        if s.valence >= p.bright_above:
            words.append("明るい")
        elif s.valence <= p.dim_below:
            words.append("沈んでいる")
        if s.arousal >= p.stirred_above:
            words.append("高ぶっている")
        elif s.arousal < p.sleepy_below and is_night(at, p):
            words.append("眠い")
        elif s.arousal <= p.calm_below:
            words.append("落ち着いている")
        if s.connection < p.lonely_below:
            words.append("人恋しい")
        return "・".join(words) or "ふだんどおり"

    # --- 会話のたび -----------------------------------------------------------------

    def feel(self, appraisal: Appraisal | None, *, source: Sequence[str], at: datetime) -> dict:
        """Masterが話したターンの評価で体の芯を動かし、1行残す。source はそのターンの会話の場所（発言と返事）。"""
        after = react(
            self.body(at),
            at,
            valence=appraisal.valence if appraisal else None,
            arousal=appraisal.arousal if appraisal else None,
            distance=appraisal.distance if appraisal else MET,
            params=self.params,
        )
        row = {
            "ts": at.astimezone(timezone.utc).isoformat(),
            "kind": "turn",
            "source": list(source),
            "feeling": appraisal.feeling if appraisal else "",
            "evaluation": appraisal.evaluation() if appraisal else None,
            "after": after.as_record(),
        }
        self.log.append(row)
        return row

    def for_pack(self, at: datetime) -> str:
        """文脈パックの⑤。気持ちの流れ・体の感じ・マスターの様子。"""
        rows = list(islice(self.log.newest_first(), SCAN_ROWS))
        lines = []
        flow = [row for row in rows if row.get("feeling")][: self.params.flow_items]
        if flow:
            lines.append("気持ちの流れ：")
            lines += [f"- {ago((at - _at(row)).total_seconds())}：{row['feeling']}" for row in reversed(flow)]
        lines.append(f"体の感じ：{self.body_words(at)}")
        seen = next((row for row in rows if (row.get("evaluation") or {}).get("master_state")), None)
        if seen is not None:
            age = (at - _at(seen)).total_seconds()
            if age <= self.params.master_state_stale_after_seconds:
                lines.append(f"マスターの様子（{ago(age)}）：{seen['evaluation']['master_state']}")
        return "\n".join(lines)

    # --- 眠り・忘れる ---------------------------------------------------------------

    def _felt(self, row: dict) -> Felt:
        at = _at(row)
        return Felt(
            at=at,
            words=row.get("feeling") or "",
            master_state=(row.get("evaluation") or {}).get("master_state") or "",
            sense=sense(Body.from_record(row["after"]), at, self.params),
            source=tuple(row.get("source") or ()),
        )

    def during(self, positions: Collection[Position]) -> list[Felt]:
        """その会話の行に拠った気持ち（古い順）。出来事のページや、その日の日記の材料。"""
        wanted = set(positions)
        days: set[str] = set()
        for day, _no in wanted:  # 気持ちの行は、会話の行と同じ日か、日をまたいだ次の日のファイルにある
            first = datetime.fromisoformat(day).date()
            days |= {first.isoformat(), (first + timedelta(days=1)).isoformat()}
        return [self._felt(row) for row in self.log.rows(days) if positions_of(row.get("source") or ()) & wanted]

    def forget(self, positions: Collection[Position]) -> int:
        return self.log.forget(positions)


def peak_end(felts: Sequence[Felt]) -> dict[str, float] | None:
    """出来事の芯の数（ピーク・エンドの法則。いちばん大きく動いたときと、終わりのときの平均。Kahneman）。"""
    if not felts:
        return None
    peak = max(felts, key=lambda felt: felt.sense.stir)
    end = felts[-1]
    return {
        "valence": round((peak.sense.valence + end.sense.valence) / 2, 3),
        "arousal": round((peak.sense.arousal + end.sense.arousal) / 2, 3),
    }


def flow_lines(felts: Iterable[Felt], limit: int) -> str:
    """気持ちの流れ（「21:05 ちょっと照れくさい」の行）。多ければ、最初と最後を含めて等間隔に limit まで選ぶ。"""
    worded = [felt for felt in felts if felt.words]
    if len(worded) > limit > 1:
        step = (len(worded) - 1) / (limit - 1)
        worded = [worded[round(i * step)] for i in range(limit)]
    return "\n".join(f"{felt.at.astimezone(JST):%H:%M} {felt.words}" for felt in worded)
