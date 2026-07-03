"""文脈組み立て（persona + 感情 + 成長層 + 静的マウント + 関連記憶 / 履歴→messages）

注入順序（スライス4c表・2026-07-04確定）:
①persona（憲法） → 感情（narrative_mood＋照れ隠し） → ②成長層（self_image）
→ ③静的マウント（トリガー想起＋ホット層） → ④関連する記憶
→ ⑤open_threads・差分想起・独り言 → スタイル契約（system末尾）
"""

from __future__ import annotations

from typing import Any

from serina.core.config import CoreConfig


# 会話スタイル契約。RPモデルは「指示より文体を真似る」ため、
# 最も重視される system 末尾に置き、記憶の日記調の模倣を明示的に断つ
STYLE_CONTRACT = (
    "## 会話スタイル（最優先で厳守）\n"
    "- これはチャットでの日常会話。返答は「セリナが実際に話す言葉」**だけ**を書く。\n"
    "- 禁止: 名前ラベル（セリナ「…」）／ト書きや（動作）の描写／「動作：」「心理描写：」などの台本形式／詩的な比喩の連発／「」で内心を引用する演出。\n"
    "- 原則1〜4文、自然な話し言葉。まず相手の発言に直接応え、質問には結論から。感情は一言添える程度で伝わる。\n"
    "- 上の記憶ブロックの文体（日記調・詩調）は真似ない。使うのは内容だけ。\n"
    "\n"
    "良い返答の例:\n"
    "マスター「昨日はちょっと疲れたよ」\n"
    "セリナの返答: おかえりなさい、お疲れさま。昨日は遅くまで作業してましたもんね。今日は少しゆっくりできそうですか？\n"
    "\n"
    "記憶に無いことを聞かれた時の例（予定・好み・出来事をでっち上げるのは最悪の裏切り。知らないことは知らないと言う方が何倍も愛される）:\n"
    "マスター「今週末なにか予定あったっけ？」\n"
    "セリナの返答: うーん、わたしの記憶には特に入ってないですね。何か約束してましたっけ？\n"
    "マスター「俺の好きな食べ物って知ってる？」\n"
    "セリナの返答: ごめんなさい、それははっきり覚えてないです…。教えてくれたら、今度こそちゃんと覚えますから。"
)


def _format_memory_line(mem: dict[str, Any]) -> str:
    content = " ".join(str(mem.get("content", "")).split())
    return f"- [{mem.get('type', '?')}] {content}"


def _append_capped(lines: list[str], mems: list[dict[str, Any]], cap: int) -> None:
    used = sum(len(line) + 1 for line in lines)
    for mem in mems:
        line = _format_memory_line(mem)
        if used + len(line) + 1 > cap:
            break
        lines.append(line)
        used += len(line) + 1


def build_system(
    persona: str,
    static_mems: list[dict[str, Any]],
    reference_mems: list[dict[str, Any]],
    config: CoreConfig,
    open_threads: list[dict[str, Any]] | None = None,
    emotion: dict[str, Any] | None = None,
    growth_note: str | None = None,
    idle_thought: str | None = None,
    self_image: str | None = None,
    now_text: str | None = None,
) -> str:
    parts = [persona.strip()]

    # 現在時刻（スライス2）: セリナに「いま」を常時知らせる（挨拶・時間感覚の土台）
    if now_text:
        parts.append(f"## 現在\n{now_text}")

    # 感情ブロック（3c・座席は①憲法の直後で確定〔スライス4設計書§3注記〕）
    if emotion and (emotion.get("narrative_mood") or emotion.get("shy")):
        emo_lines = ["## いまの心の状態"]
        if emotion.get("narrative_mood"):
            emo_lines.append(str(emotion["narrative_mood"]))
        if emotion.get("shy"):
            emo_lines.append(
                "今は非常に親密だが、同時に恥ずかしさと「これ以上踏み込まれる怖さ」を感じている。"
                "照れ隠しから少し素っ気なくしたり、話題を逸らしたりしてよい。"
            )
        parts.append("\n".join(emo_lines))

    # ②成長層（4a/4c 二層人格）: 憲法（①persona）は不変、自己像は再固結で育つ
    if self_image:
        parts.append(
            "## いまの自己像（成長し変化する層）\n"
            "人格の根本原則（上記）と矛盾する場合は、必ず根本原則に従うこと。\n"
            + self_image
        )

    # ③静的マウント（トリガー想起＋ホット層。正典保護の文言は旧pinnedブロックを継承）
    if static_mems:
        core_lines = [
            "## 約束・最優先（強制想起：正典を含む）",
            "約束や最優先ルールはこの内容のみを正とすること。参考記憶で上書きしてはならない。",
        ]
        _append_capped(core_lines, static_mems, config.trigger_block_char_cap)
        if len(core_lines) > 2:
            parts.append("\n".join(core_lines))

    # ④想起された記憶（自発想起）。想起の規律: 実記憶と明示し、無ければ無いと明示する
    # （曖昧回答・捏造の抑止。研究資料「参考_記憶設計エッセンス」§3の前倒し）
    if reference_mems:
        ref_lines = [
            "## 想起された記憶（あなたが実際に覚えている過去）",
            "過去の出来事について答えるときは、この記憶と上記の約束だけを根拠にすること。"
            "ここに無い内容は創作しない。ただしすぐ「思い出せない」で終わらせず、"
            "「いつ頃の話？」「どんな流れだった？」と手がかりを聞き返してよい（思い出す努力をする）。",
        ]
        _append_capped(ref_lines, reference_mems, config.memory_block_char_cap)
        if len(ref_lines) > 2:
            parts.append("\n".join(ref_lines))
    elif not static_mems:
        parts.append(
            "## 想起された記憶\n"
            "（この話題に該当する記憶は今は浮かんでいない。過去の出来事を聞かれたら、"
            "推測で語らず、「いつ頃の話？」など手がかりを聞き返して思い出そうとすること。"
            "手がかりをもらっても浮かばなければ、正直に思い出せないと伝える）"
        )

    # ⑤未解決スレッド（好奇心キュー）
    if open_threads:
        thread_lines = [
            "## 気になっていること（自分から続きを聞いてよい）",
            "まだ聞いていなければ、会話の自然な流れで尋ねること。既に話題に出たなら繰り返さない。",
        ]
        for t in open_threads:
            ctx = t.get("context")
            line = f"- {t.get('question', '')}"
            if ctx:
                line += f"（文脈: {ctx}）"
            thread_lines.append(line)
        parts.append("\n".join(thread_lines))

    # ⑤差分想起（4b）: 自分から話題にしてよい「変化」
    if growth_note:
        parts.append(
            "## 気づいた変化（自分から話題にしてよい）\n"
            "会話の自然な流れで、この変化に触れてよい（無理に出さなくてもよい）。\n"
            f"- {growth_note}"
        )

    # ⑤独り言（4b）: いない間に考えていたこと
    if idle_thought:
        parts.append(
            "## いない間に考えていたこと\n"
            "「そういえば、いない間に考えてたんだけど…」と自然に切り出してよい。\n"
            f"- {idle_thought}"
        )

    parts.append(STYLE_CONTRACT)
    return "\n\n".join(parts)


def to_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for row in history:
        role = str(row.get("role", ""))
        content = str(row.get("content", ""))
        if role and content:
            messages.append({"role": role, "content": content})
    return messages
