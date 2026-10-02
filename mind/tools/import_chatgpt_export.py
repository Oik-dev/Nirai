# -*- coding: utf-8 -*-
r"""ChatGPTのデータエクスポート（chat.html）の会話を、イデアの会話の生ログへ書き足す。

Serinaの最初の会話（2025-03-07〜08、lifelog/legacy/記憶/chat.html）を、今の会話と同じ形
（lifelog/conversation/<日本時間の日付>.jsonl）で残すための道具。原本の chat.html はそのまま残る。

- 会話の木から、最後に表示されていた枝（current_node から根まで）だけを取る。選ばれなかった再生成の枝は
  原本にだけ残る。
- 話者は、user＝Master、assistant＝住人の名前、tool＝道具の名前（dalle.text2im など）。system と
  画面に出ない発言は取らない。画像などの文字でない部分は「[画像]」のように置き換える。
- セッションIDは "chatgpt-<会話のID>"。何度実行しても同じ行は増えない。
- 中身はセンシティブなので、表示するのは件数と日付だけ。

    NIRAI_IDEA=<イデア> python tools/import_chatgpt_export.py            # 件数を見るだけ
    NIRAI_IDEA=<イデア> python tools/import_chatgpt_export.py --write    # 書き足す
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.idea import LIFELOG_DIR, RESIDENT_NAME  # noqa: E402
from mind.core.lifelog import MASTER, ConversationLog  # noqa: E402

DEFAULT_EXPORT = LIFELOG_DIR / "legacy" / "記憶" / "chat.html"
_MARKER = "var jsonData = "
_PLACEHOLDERS = {"image_asset_pointer": "[画像]"}


def load_conversations(path: Path) -> list[dict]:
    html = path.read_text(encoding="utf-8")
    start = html.index(_MARKER) + len(_MARKER)
    data, _ = json.JSONDecoder().raw_decode(html, start)
    return data


def _text(content: dict) -> str:
    if "parts" in content:
        pieces = []
        for part in content["parts"] or []:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                kind = str(part.get("content_type", "添付"))
                pieces.append(_PLACEHOLDERS.get(kind, f"[{kind}]"))
        return "".join(pieces)
    return str(content.get("text") or "")


def _speaker(message: dict) -> str | None:
    role = message["author"]["role"]
    if role == "user":
        return MASTER
    if role == "assistant":
        return RESIDENT_NAME
    if role == "tool":
        return str(message["author"].get("name") or "tool")
    return None


def conversation_lines(conversation: dict) -> list[dict]:
    """最後に表示されていた枝の発言を、古い順に生ログの行にする。"""
    mapping = conversation["mapping"]
    path: list[dict] = []
    node_id = conversation.get("current_node")
    while node_id:
        node = mapping[node_id]
        if node.get("message"):
            path.append(node["message"])
        node_id = node.get("parent")
    path.reverse()

    session = f"chatgpt-{conversation.get('conversation_id') or conversation.get('id')}"
    last_ts = conversation["create_time"]
    lines: list[dict] = []
    for message in path:
        if (message.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        speaker = _speaker(message)
        text = _text(message.get("content") or {})
        if speaker is None or not text.strip():
            continue
        last_ts = message.get("create_time") or last_ts
        ts = datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
        lines.append({"ts": ts, "session": session, "speaker": speaker, "text": text})
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", nargs="?", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--write", action="store_true", help="生ログへ書き足す（省略時は件数を見るだけ）")
    args = parser.parse_args()

    lines = [line for c in load_conversations(args.export) for line in conversation_lines(c)]
    speakers = Counter(line["speaker"] for line in lines)
    days = sorted({line["ts"][:10] for line in lines})
    print(f"発言 {len(lines)} 件（{', '.join(f'{k}: {v}' for k, v in speakers.items())}）")
    print(f"日付（UTC） {days[0]} 〜 {days[-1]}" if days else "発言なし")
    if args.write:
        added = ConversationLog().add_missing(lines)
        print(f"生ログへ {added} 件を書き足しました（すでにあった {len(lines) - added} 件は飛ばしました）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
