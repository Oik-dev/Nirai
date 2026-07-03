"""文脈組み立て（persona + 感情 + 静的マウント + 関連記憶 / 履歴→messages）

注入順序（スライス4c表＋3c暫定追加）:
①persona（憲法） → 感情（3c暫定・4cで座席確定） → ③静的マウント（トリガー想起＋ホット層）
→ ④関連する記憶 → ⑤open_threads
（②成長層は4a実装後にこの間へ入る）
"""

from __future__ import annotations

from typing import Any

from serina.core.config import CoreConfig


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
) -> str:
    parts = [persona.strip()]

    # 感情ブロック（3c。本格演技チューニングはスコープ外のため最小限）
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

    return "\n\n".join(parts)


def to_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for row in history:
        role = str(row.get("role", ""))
        content = str(row.get("content", ""))
        if role and content:
            messages.append({"role": role, "content": content})
    return messages
