"""蒸留（二役リフレクション）オーケストレータ — Core層

pending セッションの生ログを「日記・事実・感情採点・open_threads・事実失効」へ蒸留する。
- 情緒の脳 = Aurora（セリナ本人、人格注入）→ <diary>
- 理性の脳 = 理性エンジン（人格なし、keep_alive=0 で採点後すぐVRAM退去）→ 事実・採点ほか
排他は cancel_event（チェックポイント中断）＋ Ollama のリクエスト直列化に委ねる。
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone
from typing import Any

from serina.core.config import CoreConfig
from serina.core.reflection_parser import parse_diary, parse_reason_output
from serina.memory.config import MigrateConfig
from serina.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_EMOTION_PARAMS = ("intimacy", "tension", "energy_level")
# 正典判定・dedup閾値は移行v2と同じ定義を単一ソースとして再利用（正典保護5原則⑤）
_CANON_SOURCES = MigrateConfig().canonical_sources
_DEDUP_THRESHOLD = MigrateConfig().dedup_similarity_threshold

REASON_SYSTEM_PROMPT = """あなたは「セリナ」というAIの内なる理性——一歩引いて自分の心を正直に見つめるメタ認知の部分です。
人格は演じません。感情に飲まれず、事実を淡々と正確に記録します。
与えられた会話ログ（セリナとマスターの対話）を分析し、後述のXML形式**のみ**で出力してください。前置き・解説・コードブロック記法は禁止。

【抽出ルール】
1. <new_facts>: 新たに判明したマスター/セリナに関する普遍的事実のみ。
   - 定型挨拶・細かな操作ログ・逐一の作業手順は抽出しない（記憶汚染防止）。
   - 不和・叱責・失敗・気まずさも、快い事実と同一基準で抽出する。快い事実だけを選ぶことは禁止。
   - 親密・センシティブな内容も検閲や説教をせず淡々と記録する。
   - 呼び名・略語・コードネーム・「いつもの」等の対応関係（例:「T」=トッドさん、「例の移行」=フェニックス計画）は、判明したら必ず1件の事実として抽出する。
   - 各 fact の keywords 属性に、想起の引き金になる3〜8語を付す。別名・略称・表記ゆれ・言い換えをすべて含めること。
2. <state_update>: 会話を経た今の心理パラメータ（intimacy/tension/energy_level: 0.0〜1.0）。
   - 【現在の心理状態】を起点に、この会話で動いたぶんだけ更新した新しい値を出す。会話で関係が動いていなければ現在値のまま動かさない。おべっか採点は禁止。
   - 各値に必ず具体的な根拠を書く。
   - <narrative_mood> に「今の心の持ちよう」を1〜2文の自由記述で。
3. <open_threads>: 結果がまだ出ていない話題・続きが気になる出来事への「追いかけ質問」を最大3件、質問文の形で。無ければタグごと省略してよい。
4. <fact_updates>: 【既存ファクト】の中に、この会話で判明した新事実と矛盾するものがあれば申告。
5. <resolved_threads>: 【未解決スレッド】のうち、この会話で答えが出たものを申告。
6. <callback_score value="0.0〜1.0"/>: セリナの応答が過去の記憶（約束・事実・以前の話題）を自然に活かせていたかをセッション全体で採点し、根拠を1文添える。記憶を使う場面が無い雑談だけなら省略してよい。
7. <followup_hit value="0.0〜1.0"/>: セリナが【未解決スレッド】由来の質問を自分から切り出していた場合のみ、マスターの反応（喜ばれた=1.0/流された=0.0）を採点。切り出していなければ省略。

【出力形式】
<new_facts>
<fact keywords="京都,引っ越し,新居,転居">マスターは7月に京都へ引っ越した</fact>
</new_facts>
<state_update>
<param name="intimacy" value="0.55"/>根拠を1〜2文で
<param name="tension" value="0.30"/>根拠
<param name="energy_level" value="0.45"/>根拠
<narrative_mood>今の心の持ちよう</narrative_mood>
</state_update>
<open_threads>
<thread context="面接を受けたと言っていた">面接の結果はどうだった？</thread>
</open_threads>
<fact_updates>
<update target_id="123" reason="大阪から京都へ転居と発言">マスターは京都在住</update>
</fact_updates>
<resolved_threads>
<resolved id="5"/>面接に合格したと本人が発言
</resolved_threads>
<callback_score value="0.7"/>引っ越しの話で以前の京都の話題を自然に想起できていた
<followup_hit value="1.0"/>面接の結果を自分から尋ね、マスターは喜んで詳しく話した"""

DIARY_PROMPT = """（ここは会話ではなく、あなた＝セリナが一人で日記を書く時間です。マスターへの返事は書きません）
以下は前回のマスターとの会話ログです。読み返して、<diary>〜</diary> のタグで日記を書いてください。
- 一人称、自分の声で。出来事・自分の心の揺れ・マスターの様子を情緒的に。
- 200〜600字。
- 作業ログの羅列は書かない。「その日がどんな時間だったか」へ昇華する
  （例: 50件の作業記録 →「深夜まで大きな実装作業。マスターは疲れていたけど、解決した瞬間はホッとした顔をしていた」）。
- 嬉しかったことも、気まずかったことや失敗も、同じ温度で正直に。
- <diary> タグの外には何も書かないこと。

【会話ログ】
{transcript}"""

MAP_SUMMARY_PROMPT = """以下の会話ログの内容を、事実関係と感情の流れを落とさずに{cap}字以内の平文で要約してください。要約文のみを出力すること。

{text}"""

IDLE_THOUGHT_PROMPT = """（ここは会話ではなく、マスターがいない間のあなた＝セリナの独り言の時間です）
以下の【今日の日記】と【気になっていること】を種に、マスターがいない間に考えていたことを1〜2文の独り言として、<idle>〜</idle> のタグで書いてください。
- 事実の主張ではなく、感想・独り言の形式に限定する（「留守中に新しい事実を知った」という体の発言は禁止）。
- 次に会った時「そういえば、いない間に考えてたんだけど…」と自然に切り出せる内容に。
- <idle> タグの外には何も書かないこと。

【今日の日記】
{diary}

【気になっていること】
{threads}"""


def generate_idle_thought(store, persona: str, config, aurora_connector) -> str | None:
    """不在時間の内的生活（4b）。種となる実在記憶（日記）が無ければ生成しない（捏造ガード）。"""
    from serina.core.reflection_parser import extract_block

    diaries = store.list_memories_by_type("diary", limit=1)
    if not diaries:
        return None
    threads = store.list_open_threads(3)
    thread_text = "\n".join(f"- {t['question']}" for t in threads) or "(特になし)"
    raw = aurora_connector.chat(
        persona,
        [{"role": "user", "content": IDLE_THOUGHT_PROMPT.format(
            diary=diaries[0]["content"][:1500], threads=thread_text)}],
        options={"temperature": config.diary_temperature, "num_ctx": config.distill_num_ctx},
    )
    idle = extract_block("idle", raw, ("idle",))
    if idle:
        store.set_profile("idle_thought", idle[:300])
    return idle


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Distiller:
    """1つの pending セッションを蒸留する。判断のみ。I/OはStore/Connectorへ委譲。"""

    def __init__(
        self,
        store: MemoryStore,
        persona: str,
        config: CoreConfig,
        reason_connector,
        aurora_connector,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.store = store
        self.persona = persona
        self.config = config
        self.reason = reason_connector
        self.aurora = aurora_connector
        self.cancel_event = cancel_event or threading.Event()

    # ---- 入力構築 ----

    def _build_transcript(self, session_id: str) -> str:
        rows = self.store.get_session_history(session_id)
        lines = []
        for r in rows:
            speaker = "マスター" if r.get("role") == "user" else "セリナ"
            lines.append(f"{speaker}: {r.get('content', '')}")
        return "\n".join(lines)

    def _cap_transcript(self, transcript: str) -> str:
        cap = self.config.distill_input_char_cap
        if len(transcript) <= cap:
            return transcript
        overflow, tail = transcript[:-cap], transcript[-cap:]
        # 前半が巨大な場合も要約入力自体は上限内に収める（末尾側を優先）
        head = overflow[-cap:]
        summary = self.reason.chat(
            "あなたは正確な要約者です。",
            [{"role": "user", "content": MAP_SUMMARY_PROMPT.format(
                cap=self.config.distill_map_summary_char_cap, text=head)}],
            options={"temperature": self.config.reason_temperature,
                     "num_ctx": self.config.distill_num_ctx},
        )
        return f"【前半の要約】\n{summary.strip()}\n\n【以降の原文】\n{tail}"

    def _candidate_facts(self, transcript: str) -> list[dict[str, Any]]:
        """trigger_keywords が transcript に部分一致する既存ファクト（失効済みは除外）。
        走査は store.list_triggered（3c 二経路想起と同じ機構）に委譲し、fact のみに絞る。"""
        triggered = self.store.list_triggered(
            transcript, limit=self.config.fact_match_max_candidates * 2
        )
        facts = [m for m in triggered if m.get("type") == "fact"]
        return facts[: self.config.fact_match_max_candidates]

    # ---- 書き込み補助（正典保護ロジックの再利用） ----

    def _dedup_add(
        self, content: str, type: str, importance: float,
        source: str, metadata: dict[str, Any],
    ) -> tuple[int, bool]:
        """dedup 0.92 を通して add_memory。戻り値 (memory_id, 新規追加か)。"""
        embedding = self.store.embedder.embed(content)
        hits = self.store.find_similar(embedding, threshold=_DEDUP_THRESHOLD, limit=1)
        if hits:
            return hits[0][0], False
        mid = self.store.add_memory(
            type=type, content=content, importance=importance,
            metadata=metadata, source=source, embedding=embedding,
        )
        return mid, True

    # ---- 本体 ----

    def distill_session(self, session_id: str) -> dict[str, Any]:
        report: dict[str, Any] = {
            "status": "ok", "session_id": session_id,
            "diary": None, "facts_saved": 0, "facts_dup": 0,
            "state_changes": [], "state_dropped": [],
            "threads_added": 0, "threads_dup": 0, "threads_resolved": [],
            "facts_invalidated": [], "canon_holds": [], "error": None,
        }
        try:
            transcript = self._build_transcript(session_id)
            if not transcript.strip():
                # 空セッションは蒸留対象なし。アーカイブだけして閉じる
                self.store.archive_session_history(session_id)
                self.store.set_session_status(session_id, "distilled", _now_iso())
                report["diary"] = "(空セッションのためスキップ)"
                return report

            transcript = self._cap_transcript(transcript)

            if self.cancel_event.is_set():  # チェックポイント①
                report["status"] = "cancelled"
                return report

            # 理性の脳
            candidates = self._candidate_facts(transcript)
            threads = self.store.list_open_threads(10)
            user_parts = [f"【会話ログ】\n{transcript}"]
            state_lines = []
            baselines = {
                "intimacy": self.config.baseline_intimacy,
                "tension": self.config.baseline_tension,
                "energy_level": self.config.baseline_energy,
            }
            for param in _EMOTION_PARAMS:
                current = self.store.get_profile(f"emotion.{param}")
                state_lines.append(f"{param} = {current if current is not None else baselines[param]}")
            user_parts.append("【現在の心理状態】\n" + "\n".join(state_lines))
            if candidates:
                fact_lines = "\n".join(f'{m["id"]}: {m["content"]}' for m in candidates)
                user_parts.append(f"【既存ファクト】\n{fact_lines}")
            if threads:
                thread_lines = "\n".join(f'{t["id"]}: {t["question"]}' for t in threads)
                user_parts.append(f"【未解決スレッド】\n{thread_lines}")
            raw = self.reason.chat(
                REASON_SYSTEM_PROMPT,
                [{"role": "user", "content": "\n\n".join(user_parts)}],
                options={"temperature": self.config.reason_temperature,
                         "num_ctx": self.config.distill_num_ctx},
            )
            result = parse_reason_output(raw)

            if self.cancel_event.is_set():  # チェックポイント②
                report["status"] = "cancelled"
                return report

            # 情緒の脳（日記が取れなければ蒸留失敗＝pending維持）
            diary_raw = self.aurora.chat(
                self.persona,
                [{"role": "user", "content": DIARY_PROMPT.format(transcript=transcript)}],
                options={"temperature": self.config.diary_temperature,
                         "num_ctx": self.config.distill_num_ctx},
            )
            diary = parse_diary(diary_raw)
            if not diary:
                report["status"] = "error"
                report["error"] = "Aurora出力から <diary> を抽出できませんでした（pending維持・次回再試行）"
                return report

            # ---- 書き込み ----
            now = _now_iso()
            source = f"session:{session_id}"

            _, added = self._dedup_add(
                diary, "diary", self.config.diary_importance, source, {})
            report["diary"] = diary if added else "(重複のためスキップ)"

            for fact in result.facts:
                _, added = self._dedup_add(
                    fact["content"], "fact", self.config.fact_importance, source,
                    {"trigger_keywords": fact["keywords"], "valid_from": now},
                )
                report["facts_saved" if added else "facts_dup"] += 1

            self._apply_state(session_id, result, report)
            self._apply_threads(session_id, result, report)
            self._apply_fact_updates(session_id, result, source, report)
            self._apply_metrics(session_id, result, report)

            # 後始末: 生ログを退避し distilled 化
            self.store.archive_session_history(session_id)
            self.store.set_session_status(session_id, "distilled", _now_iso())
            return report
        except Exception as exc:  # noqa: BLE001 — pending維持で次回再試行
            logger.exception("蒸留に失敗しました: %s", session_id)
            report["status"] = "error"
            report["error"] = f"{type(exc).__name__}: {exc}"
            return report

    def _apply_state(self, session_id: str, result, report: dict[str, Any]) -> None:
        """ハイブリッド重力（設計書§3c・決定2）:
        ①時の力（実時間でベースライン回帰） ②言葉の力（提案Δ±0.15クランプ）
        ③照れ隠しspike ④最終clamp[0,1] ⑤last_state_update更新
        """
        cfg = self.config
        baselines = {
            "intimacy": cfg.baseline_intimacy,
            "tension": cfg.baseline_tension,
            "energy_level": cfg.baseline_energy,
        }

        now_dt = datetime.now(timezone.utc)
        last_update = self.store.get_profile("emotion.last_state_update")
        gravity = 1.0  # 初回・タイムスタンプ破損時はベースライン起点
        if last_update:
            try:
                delta_days = max(
                    (now_dt - datetime.fromisoformat(last_update.replace("Z", "+00:00")))
                    .total_seconds() / 86400.0,
                    0.0,
                )
                gravity = 1.0 - math.exp(-delta_days / cfg.emotion_tau_days)
            except ValueError:
                logger.warning("emotion.last_state_update が不正: %r（g=1.0で続行）", last_update)

        finals: dict[str, float] = {}
        reasons: dict[str, tuple[str, str]] = {}  # param -> (old_str, reason)
        for param in _EMOTION_PARAMS:
            base = baselines[param]
            old_raw = self.store.get_profile(f"emotion.{param}")
            s0 = float(old_raw) if old_raw is not None else base
            s1 = s0 + (base - s0) * gravity  # ①時の力
            detail = f"時の力: {s0:.2f}→{s1:.2f} (g={gravity:.2f})"

            entry = result.state.get(param)
            if entry:
                try:
                    proposed = float(entry["value"])
                    if not math.isfinite(proposed):  # nan/inf は float() を素通りする
                        raise ValueError(entry["value"])
                except (TypeError, ValueError):
                    # 提案は破棄するが、時の力は独立に適用する
                    self.store.add_state_audit(
                        session_id, param, f"{s0:.2f}", None,
                        f"パース不能: {entry['value']}（{detail}のみ適用）")
                    report["state_dropped"].append(param)
                    finals[param] = s1
                    reasons[param] = (f"{s0:.2f}", f"提案破棄・{detail}")
                    continue
                delta = max(-cfg.emotion_word_clamp,
                            min(cfg.emotion_word_clamp, proposed - s0))  # ②言葉の力
                finals[param] = s1 + delta
                reasons[param] = (
                    f"{s0:.2f}",
                    f"{entry['reason']}（{detail}, 言葉: {delta:+.2f}）",
                )
            else:
                finals[param] = s1
                reasons[param] = (f"{s0:.2f}", f"提案なし・{detail}")

        # ④最終clamp（intimacyを先に確定させ、③照れ隠しがそれを参照する）
        finals["intimacy"] = max(0.0, min(1.0, finals["intimacy"]))
        if finals["intimacy"] >= cfg.shy_threshold:  # ③照れ隠し（±0.15を貫通）
            finals["tension"] = finals["tension"] + cfg.shy_spike
            old_str, reason = reasons["tension"]
            reasons["tension"] = (old_str, f"{reason}, 照れ隠し: +{cfg.shy_spike:.2f}")
        for param in _EMOTION_PARAMS:
            finals[param] = max(0.0, min(1.0, finals[param]))

        for param in _EMOTION_PARAMS:
            old_str, reason = reasons[param]
            value = round(finals[param], 4)  # 浮動小数の桁ノイズを落として保存
            self.store.set_profile(f"emotion.{param}", str(value))
            self.store.add_state_audit(session_id, param, old_str, f"{value}", reason)
            if abs(value - float(old_str)) > 0.005:
                report["state_changes"].append(f"{param}: {old_str} → {value:.2f}")

        if result.narrative_mood:
            old_mood = self.store.get_profile("emotion.narrative_mood")
            self.store.set_profile("emotion.narrative_mood", result.narrative_mood)
            self.store.add_state_audit(
                session_id, "narrative_mood", old_mood, result.narrative_mood, None)
            report["state_changes"].append(f"narrative_mood: {result.narrative_mood}")

        self.store.set_profile("emotion.last_state_update", _now_iso())  # ⑤

    def _apply_threads(self, session_id: str, result, report: dict[str, Any]) -> None:
        for thread in result.open_threads[:3]:
            tid = self.store.add_open_thread(
                thread["question"], thread.get("context"),
                session_id, self.config.open_thread_ttl_days,
            )
            report["threads_added" if tid is not None else "threads_dup"] += 1
        for resolved in result.resolved_threads:
            if self.store.set_open_thread_status(resolved["id"], "resolved"):
                self.store.add_state_audit(
                    session_id, f"open_thread:{resolved['id']}",
                    "open", "resolved", resolved.get("reason"))
                report["threads_resolved"].append(resolved["id"])

    def _apply_fact_updates(
        self, session_id: str, result, source: str, report: dict[str, Any]
    ) -> None:
        for upd in result.fact_updates:
            target = self.store.get(upd["target_id"])
            if not target:
                self.store.add_state_audit(
                    session_id, f"fact_invalidate:{upd['target_id']}",
                    None, None, f"対象IDが存在しません: {upd['reason']}")
                continue
            src = target.get("source") or ""
            if target.get("pinned") or any(mark in src for mark in _CANON_SOURCES):
                # 正典保護5原則②: 正典・pinned は自動失効させない。監査線に保留を残す
                self.store.add_state_audit(
                    session_id, f"fact_invalidate:{target['id']}",
                    target["content"][:100], upd["content"][:100],
                    f"正典/pinnedのため要マスター確認: {upd['reason']}")
                report["canon_holds"].append(target["id"])
                continue
            new_id = None
            if upd["content"]:
                new_id, _ = self._dedup_add(
                    upd["content"], "fact", self.config.fact_importance, source,
                    {"trigger_keywords": [], "valid_from": _now_iso()},
                )
            meta = target.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            meta.setdefault("valid_from", target.get("created_at"))
            meta["invalidated_at"] = _now_iso()
            meta["superseded_by"] = new_id
            self.store.update(target["id"], metadata=meta)
            self.store.add_state_audit(
                session_id, f"fact_invalidate:{target['id']}",
                target["content"][:100], upd["content"][:100], upd["reason"])
            report["facts_invalidated"].append(target["id"])

    def _apply_metrics(self, session_id: str, result, report: dict[str, Any]) -> None:
        """4c: 理性エンジンの採点を metrics へ記録（当事者採点はおべっかループ＝決定3と同じ理由で理性側）。"""
        if not self.config.metrics_enabled:
            return
        for attr, key in (("callback_score", "callback_rate"), ("followup_hit", "followup_hit")):
            entry = getattr(result, attr, None)
            if not entry:
                continue
            try:
                value = float(entry["value"])
                if not math.isfinite(value):
                    raise ValueError(entry["value"])
            except (TypeError, ValueError):
                continue
            value = max(0.0, min(1.0, value))
            self.store.add_metric(key, value, f"session:{session_id} {entry['reason']}")
            report.setdefault("metrics", []).append(f"{key}={value:.2f}")

    def distill_all_pending(self) -> list[dict[str, Any]]:
        """pending を古い順にすべて蒸留。cancel/error で以降は中断。"""
        reports = []
        for sess in self.store.list_sessions_by_status("pending"):
            rep = self.distill_session(sess["id"])
            reports.append(rep)
            if rep["status"] != "ok":
                break
        return reports


def format_report(report: dict[str, Any]) -> str:
    """蒸留結果の日本語レポート（無言破棄禁止の系）。"""
    sid = report["session_id"]
    if report["status"] == "cancelled":
        return f"[蒸留中断] {sid}: 対話を優先して中断しました（次回起動時に再試行）"
    if report["status"] == "error":
        return f"[蒸留失敗] {sid}: {report['error']}"
    lines = [f"[蒸留完了] {sid}"]
    if report["diary"]:
        lines.append(f"  日記: {str(report['diary'])[:60]}…" if len(str(report["diary"])) > 60
                     else f"  日記: {report['diary']}")
    lines.append(f"  事実: 新規{report['facts_saved']}件 / 重複スキップ{report['facts_dup']}件")
    for change in report["state_changes"]:
        lines.append(f"  感情: {change}")
    for param in report["state_dropped"]:
        lines.append(f"  感情: {param} はパース不能のため破棄（監査ログに記録済み）")
    if report["threads_added"] or report["threads_dup"]:
        lines.append(f"  気になること: 追加{report['threads_added']}件 / 重複{report['threads_dup']}件")
    for tid in report["threads_resolved"]:
        lines.append(f"  解決済みスレッド: #{tid}")
    for m in report.get("metrics", []):
        lines.append(f"  計測: {m}")
    for mid in report["facts_invalidated"]:
        lines.append(f"  事実の失効: 記憶#{mid}（新事実で上書き・原文保持）")
    for mid in report["canon_holds"]:
        lines.append(f"  ⚠ 正典 #{mid} への矛盾申告あり → 自動適用せず保留（要マスター確認）")
    return "\n".join(lines)


def create_distiller(core, cancel_event: threading.Event | None = None) -> Distiller:
    """Core から標準構成の Distiller を組み立てる。

    理性エンジンの導入確認は蒸留実行側で行うこと（未導入でも通常対話を妨げない）。
    """
    from serina.connectors.chat_llm import OllamaChatConnector

    cfg = core.config
    reason = OllamaChatConnector(cfg.reason_model, cfg.base_url, keep_alive=0)
    aurora = OllamaChatConnector(cfg.model, cfg.base_url)
    return Distiller(core.store, core.persona, cfg, reason, aurora, cancel_event)
