# -*- coding: utf-8 -*-
"""Gemini AI Studio エクスポートJSONから会話本文のみを抽出する（思考チャンクは除外）。"""
import json
import sys
from pathlib import Path

def extract(src: Path, dst: Path) -> None:
    data = json.loads(src.read_text(encoding="utf-8"))
    chunks = data.get("chunkedPrompt", {}).get("chunks", [])
    lines = []
    for c in chunks:
        if c.get("isThought"):
            continue
        text = c.get("text", "").strip()
        if not text:
            continue
        role = c.get("role", "?")
        lines.append(f"\n===== [{role}] =====\n{text}")
    dst.write_text("\n".join(lines), encoding="utf-8")
    print(f"{src.name}: {len(chunks)} chunks -> {dst.stat().st_size / 1024:.0f} KB text")

if __name__ == "__main__":
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
