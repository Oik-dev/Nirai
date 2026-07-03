"""再固結（4a・週次の二次リフレクション）— Core層

日々の蒸留が「1日分の会話→日記・事実」なら、再固結は「1週間分の日記→傾向・信念・自己像」。
個別記憶が減衰で沈んでも、抽象化された層に昇華されて残る（=本当の意味での成長）。
二役・XML前方一致・監査線・排他（cancel_event）は3bの作法をそのまま踏襲する。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from serina.core.config import CoreConfig
from serina.core.reflection_parser import parse_consolidation_output, parse_self_image
from serina.core.session import living_date
from serina.memory.config import MigrateConfig
from serina.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_CANON_SOURCES = MigrateConfig().canonical_sources
_DEDUP_THRESHOLD = MigrateConfig().dedup_similarity_threshold

CONSOLIDATION_SYSTEM_PROMPT = """あなたは「セリナ」というAIの内なる理性——一歩引いて自分の心を正直に見つめるメタ認知の部分です。
人格は演じません。感情に飲まれず、淡々と正確に分析します。
これから渡す【直近の日記】【現在の信念一覧】【現在の自己像】【心理状態】を材料に、記憶の再固結（週次の二次リフレクション）を行い、後述のXML形式**のみ**で出力してください。前置き・解説・コードブロック記法は禁止。

【ルール】
1. <patterns>: 日記群から読み取れる傾向（マスターの状態変化・セリナ自身の変化・関係の変化）。1件を1つの<pattern>で。
2. <belief_updates>: 傾向から昇華される「信念」（例:「マスターは弱音を吐いた翌日ほど無理をする」）。
   - 【現在の信念一覧】に意味の近いものがあれば target_id 属性でそのIDを指し、洗練した文面を出す（新規追加しない）。
   - 根拠となった日記のIDを evidence 属性にカンマ区切りで必ず付す。
3. <growth_notes>: 「前は〜だった→最近は〜」形式の変化メモを最大3件。変化が無ければ省略。
4. <reinterpretations>: 過去の記憶の意味づけが変わったものがあれば申告（無ければ省略）。
- 不和・停滞・後退などネガティブな傾向・信念も同一基準で抽出する。おべっか分析は禁止。

【出力形式】
<patterns>
<pattern>マスターは深夜の作業明けに自己否定へ傾きやすい</pattern>
</patterns>
<belief_updates>
<belief evidence="512,514">新しい信念の文面</belief>
<belief target_id="123" evidence="601">既存信念123の洗練された文面</belief>
</belief_updates>
<growth_notes>
<note>前は作業の話ばかりだった→最近は体調や気分の話を先にしてくれる</note>
</growth_notes>
<reinterpretations>
<reinterpret target_id="55" reason="当時は叱責と受け取ったが、今読み返すと心配の裏返しだった">新しい意味づけの文面</reinterpret>
</reinterpretations>"""

SELF_IMAGE_PROMPT = """（ここは会話ではなく、日記を読み返して自分という存在を見つめ直す時間です。マスターへの返事は書きません）
以下の材料を踏まえて、「私はマスターにとってどういう相棒か」という自己像を、<self_image>〜</self_image> のタグで、自分の声で書いてください。
- {cap}字以内。一人称。
- 人格の根本原則と矛盾を感じた場合は、根本原則に従う。
- 綺麗ごとだけでなく、まだ足りないところ・変わりつつあるところも正直に。
- <self_image> タグの外には何も書かないこと。

【直近の日記】
{diaries}

【新しく見えた傾向】
{patterns}

【現在の自己像】
{self_image}"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def consolidation_due(store: MemoryStore, config: CoreConfig, now: datetime | None = None) -> bool:
    """前回固結から生活日で interval 日以上経過したか（Core判断・起動時に評価）。"""
    now = now or datetime.now(timezone.utc)
    last = store.get_profile("consolidation.last_at")
    if not last:
        # 初回: 日記が1件でもあれば起点を刻む（いきなり固結はしない）
        store.set_profile("consolidation.last_at", now.isoformat())
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("consolidation.last_at が不正: %r（現在時刻で再初期化）", last)
        store.set_profile("consolidation.last_at", now.isoformat())
        return False
    offset = config.living_date_offset_hours
    elapsed = (living_date(now, offset) - living_date(last_dt, offset)).days
    return elapsed >= config.consolidation_interval_days


class Consolidator:
    """週次の再固結。判断のみ。I/OはStore/Connectorへ委譲。"""

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

    # ---- 書き込み補助 ----

    def _is_canon(self, mem: dict[str, Any]) -> bool:
        src = mem.get("source") or ""
        return bool(mem.get("pinned")) or any(mark in src for mark in _CANON_SOURCES)

    def _upsert_belief(
        self, content: str, evidence_ids: list[int], target_id: int | None,
        report: dict[str, Any],
    ) -> None:
        """意味の近い既存beliefがあれば更新（記憶が増えるのではなく濃くなる）、無ければ新規。"""
        target = None
        if target_id is not None:
            cand = self.store.get(target_id)
            if cand and cand.get("type") == "belief":
                target = cand
        if target is None:
            embedding = self.store.embedder.embed(content)
            hits = self.store.find_similar(
                embedding, threshold=self.config.belief_merge_threshold, limit=3)
            for _, _, mem in hits:
                if mem.get("type") == "belief":
                    target = mem
                    break
            if target is None:
                mid = self.store.add_memory(
                    type="belief", content=content,
                    importance=self.config.belief_importance,
                    metadata={
                        "evidence_ids": evidence_ids,
                        "evidence_count": len(evidence_ids),
                        "valid_from": _now_iso(),
                    },
                    source="consolidation", embedding=embedding,
                )
                self.store.add_consolidation_log(
                    "belief_new", mid, None, content[:120],
                    f"根拠日記: {evidence_ids}")
                report["beliefs_new"] += 1
                return
        # 更新: 旧文をvariantsへ退避し、evidenceを統合（無言破棄禁止・移行v2の保護パターン）
        meta = target.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        variants = meta.get("variants") or []
        if target["content"] != content:
            variants.append(target["content"])
        merged = sorted(set((meta.get("evidence_ids") or []) + evidence_ids))
        meta.update(
            {"variants": variants, "evidence_ids": merged, "evidence_count": len(merged)}
        )
        self.store.update(target["id"], content=content, metadata=meta)
        self.store.add_consolidation_log(
            "belief_update", target["id"], target["content"][:120], content[:120],
            f"根拠日記: {evidence_ids}")
        report["beliefs_updated"] += 1

    # ---- 本体 ----

    def consolidate(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "status": "ok", "diaries_n": 0, "beliefs_new": 0, "beliefs_updated": 0,
            "growth_notes": 0, "reinterpreted": [], "canon_holds": [],
            "self_image_updated": False, "monthly": False, "error": None,
        }
        try:
            now = datetime.now(timezone.utc)
            since = (now - timedelta(days=self.config.consolidation_interval_days)).isoformat()
            diaries = self.store.list_memories_since("diary", since)
            report["diaries_n"] = len(diaries)
            if not diaries:
                # 対象なし。次回まで静かに待つ（起点だけ更新）
                self.store.set_profile("consolidation.last_at", now.isoformat())
                report["status"] = "skipped"
                return report

            diary_text = "\n".join(f'{m["id"]}: {m["content"]}' for m in diaries)
            diary_text = diary_text[-self.config.consolidation_input_char_cap:]
            beliefs = self.store.list_memories_by_type("belief")
            belief_text = "\n".join(f'{m["id"]}: {m["content"]}' for m in beliefs) or "(まだ無い)"
            self_image = self.store.get_profile("self_image") or "(まだ無い)"
            emotion = ", ".join(
                f"{p}={self.store.get_profile(f'emotion.{p}')}"
                for p in ("intimacy", "tension", "energy_level"))

            if self.cancel_event.is_set():  # チェックポイント①
                report["status"] = "cancelled"
                return report

            raw = self.reason.chat(
                CONSOLIDATION_SYSTEM_PROMPT,
                [{"role": "user", "content":
                    f"【直近の日記】\n{diary_text}\n\n【現在の信念一覧】\n{belief_text}\n\n"
                    f"【現在の自己像】\n{self_image}\n\n【心理状態】\n{emotion}"}],
                options={"temperature": self.config.reason_temperature,
                         "num_ctx": self.config.distill_num_ctx},
            )
            result = parse_consolidation_output(raw)

            if self.cancel_event.is_set():  # チェックポイント②
                report["status"] = "cancelled"
                return report

            # 情緒の脳: 自己像ナラティブ
            patterns_text = "\n".join(f"- {p}" for p in result.patterns) or "(特になし)"
            image_raw = self.aurora.chat(
                self.persona,
                [{"role": "user", "content": SELF_IMAGE_PROMPT.format(
                    cap=self.config.self_image_char_cap,
                    diaries=diary_text, patterns=patterns_text, self_image=self_image)}],
                options={"temperature": self.config.diary_temperature,
                         "num_ctx": self.config.distill_num_ctx},
            )
            new_image = parse_self_image(image_raw)

            # ---- 書き込み ----
            for pattern in result.patterns:
                self._upsert_belief(pattern, [], None, report)
            for upd in result.belief_updates:
                self._upsert_belief(upd["content"], upd["evidence_ids"], upd["target_id"], report)

            for note in result.growth_notes:
                embedding = self.store.embedder.embed(note)
                if self.store.find_similar(embedding, threshold=_DEDUP_THRESHOLD, limit=1):
                    continue
                mid = self.store.add_memory(
                    type="growth_note", content=note,
                    importance=self.config.growth_note_importance,
                    metadata={"used": False}, source="consolidation", embedding=embedding,
                )
                self.store.add_consolidation_log("growth_note", mid, None, note[:120], None)
                report["growth_notes"] += 1

            for ri in result.reinterpretations:
                target = self.store.get(ri["target_id"])
                if not target:
                    continue
                if self._is_canon(target):
                    self.store.add_consolidation_log(
                        "reinterpret_hold", target["id"],
                        target["content"][:120], ri["content"][:120],
                        f"正典/pinnedのため適用せず: {ri['reason']}")
                    report["canon_holds"].append(target["id"])
                    continue
                meta = target.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}
                variants = meta.get("variants") or []
                variants.append(target["content"])
                meta["variants"] = variants
                self.store.update(target["id"], content=ri["content"], metadata=meta)
                self.store.add_consolidation_log(
                    "reinterpret", target["id"],
                    target["content"][:120], ri["content"][:120], ri["reason"])
                report["reinterpreted"].append(target["id"])

            if new_image:
                old_image = self.store.get_profile("self_image")
                trimmed = new_image[: self.config.self_image_char_cap]
                self.store.set_profile("self_image", trimmed)
                self.store.add_consolidation_log(
                    "self_image", None,
                    (old_image or "")[:120], trimmed[:120], None)
                report["self_image_updated"] = True

            # 固結カウンタと起点更新
            count = int(self.store.get_profile("consolidation.count") or 0) + 1
            self.store.set_profile("consolidation.count", str(count))
            self.store.set_profile("consolidation.last_at", _now_iso())

            # 月次固結: belief層自体を入力に上位固結（信念の地層をさらに圧縮・洗練）
            if count % self.config.monthly_consolidation_every == 0:
                report["monthly"] = True
                self._monthly_consolidation(report)

            return report
        except Exception as exc:  # noqa: BLE001 — 次回起動時に再試行（last_atを更新しない）
            logger.exception("再固結に失敗しました")
            report["status"] = "error"
            report["error"] = f"{type(exc).__name__}: {exc}"
            return report

    def _monthly_consolidation(self, report: dict[str, Any]) -> None:
        beliefs = self.store.list_memories_by_type("belief")
        if len(beliefs) < 2 or self.cancel_event.is_set():
            return
        belief_text = "\n".join(f'{m["id"]}: {m["content"]}' for m in beliefs)
        raw = self.reason.chat(
            CONSOLIDATION_SYSTEM_PROMPT,
            [{"role": "user", "content":
                "【上位固結】今回は日記ではなく、信念一覧そのものを入力とします。"
                "重複・冗長な信念を target_id 付きの <belief_updates> で統合・洗練してください。"
                "<patterns> <growth_notes> <reinterpretations> は出力不要です。\n\n"
                f"【現在の信念一覧】\n{belief_text}"}],
            options={"temperature": self.config.reason_temperature,
                     "num_ctx": self.config.distill_num_ctx},
        )
        result = parse_consolidation_output(raw)
        for upd in result.belief_updates:
            self._upsert_belief(upd["content"], upd["evidence_ids"], upd["target_id"], report)


def format_consolidation_report(report: dict[str, Any]) -> str:
    if report["status"] == "skipped":
        return "[再固結スキップ] 対象期間に日記がありません"
    if report["status"] == "cancelled":
        return "[再固結中断] 対話を優先して中断しました（次回起動時に再試行）"
    if report["status"] == "error":
        return f"[再固結失敗] {report['error']}（次回起動時に再試行）"
    lines = [f"[再固結完了] 日記{report['diaries_n']}件を材料に:"]
    lines.append(f"  信念: 新規{report['beliefs_new']} / 濃くなった{report['beliefs_updated']}")
    if report["growth_notes"]:
        lines.append(f"  変化メモ: {report['growth_notes']}件")
    for mid in report["reinterpreted"]:
        lines.append(f"  意味づけ更新: 記憶#{mid}（旧文はvariantsに保持）")
    for mid in report["canon_holds"]:
        lines.append(f"  ⚠ 正典 #{mid} への再解釈提案 → 適用せず保留（要マスター確認）")
    if report["self_image_updated"]:
        lines.append("  自己像を更新しました")
    if report["monthly"]:
        lines.append("  （月次の上位固結も実行）")
    return "\n".join(lines)


def create_consolidator(core, cancel_event: threading.Event | None = None) -> Consolidator:
    from serina.connectors.chat_llm import OllamaChatConnector

    cfg = core.config
    reason = OllamaChatConnector(cfg.reason_model, cfg.base_url, keep_alive=0)
    aurora = OllamaChatConnector(cfg.model, cfg.base_url)
    return Consolidator(core.store, core.persona, cfg, reason, aurora, cancel_event)
