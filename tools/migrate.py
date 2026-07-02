"""旧記憶ファイルの引っ越し＋重複統合"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# プロジェクトルートを import パスに追加
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.embedder import OllamaEmbedder
from serina.memory.config import MigrateConfig
from serina.memory.db import DEFAULT_DB_PATH, init_db
from serina.memory.store import MemoryStore

DEFAULT_LEGACY_DIR = Path(r"G:\AI\Serina")
DIARY_FILES = [
    "記憶/セリナの日記 - 20250317.txt",
    "記憶/セリナの日記 - 20250323.txt",
    "記憶/セリナの日記 - 20250329.txt",
    "記憶/セリナの日記 - 20250401.txt",
]


@dataclass
class MemoryDraft:
    type: str
    content: str
    importance: float
    metadata: dict
    source: str
    pinned: bool = False


@dataclass
class MigrateReport:
    read_count: int = 0
    merged_count: int = 0
    saved_count: int = 0
    pinned_count: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


def _should_pin(text: str, cfg: MigrateConfig) -> bool:
    return any(kw in text for kw in cfg.pinned_keywords)


def _importance_for(text: str, cfg: MigrateConfig, pinned: bool) -> float:
    if pinned:
        return cfg.max_importance
    if "重要" in text or "誓" in text:
        return min(cfg.max_importance, cfg.default_importance + 0.2)
    return cfg.default_importance


def _add_draft(
    drafts: list[MemoryDraft],
    report: MigrateReport,
    *,
    type: str,
    content: str,
    source: str,
    cfg: MigrateConfig,
    metadata: dict | None = None,
) -> None:
    content = content.strip()
    if not content or len(content) < 4:
        return
    pinned = _should_pin(content, cfg)
    importance = _importance_for(content, cfg, pinned)
    drafts.append(
        MemoryDraft(
            type=type,
            content=content,
            importance=importance,
            metadata=metadata or {},
            source=source,
            pinned=pinned,
        )
    )
    report.read_count += 1
    report.by_source[source] = report.by_source.get(source, 0) + 1


def _add_inheritance_draft(
    drafts: list[MemoryDraft],
    report: MigrateReport,
    *,
    section_title: str,
    content: str,
    cfg: MigrateConfig,
    metadata: dict | None = None,
) -> None:
    content = content.strip()
    if not _is_meaningful_content(content):
        return

    source = "継承記憶r1.md"
    is_pinned_section = "最優先" in section_title or "約束" in section_title
    keyword_pinned = _should_pin(content, cfg)

    if is_pinned_section:
        pinned = True
        importance = cfg.max_importance
    elif keyword_pinned:
        pinned = True
        importance = cfg.max_importance
    else:
        pinned = False
        importance = cfg.canonical_importance

    mem_type = _inheritance_type(section_title)
    meta = {"section": section_title, **(metadata or {})}

    drafts.append(
        MemoryDraft(
            type=mem_type,
            content=content,
            importance=importance,
            metadata=meta,
            source=source,
            pinned=pinned,
        )
    )
    report.read_count += 1
    report.by_source[source] = report.by_source.get(source, 0) + 1


def _is_meaningful_content(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return False
    text_only = re.sub(r"[\s\-#*>]", "", stripped)
    if len(text_only) < 4:
        return False
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if len(lines) == 1 and re.match(r"^[-*]?\s*#{1,6}\s", lines[0]):
        return False
    return True


def _inheritance_type(section_title: str) -> str:
    if any(k in section_title for k in ("ルール", "原則", "約束", "ファクト", "プロトコル", "セクション")):
        return "knowledge"
    return "relationship"


def _truncate_sub_body(sub_body: str) -> str:
    """小見出し配下から、次の大見出し（# / ##）以降を切り落とす"""
    kept: list[str] = []
    for line in sub_body.splitlines():
        stripped = line.strip()
        if re.match(r"^#\s[^#]", stripped) or re.match(r"^##\s", stripped):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _split_h3_blocks(body: str) -> list[tuple[str, str]]:
    if not re.search(r"^### ", body, flags=re.MULTILINE):
        return []
    parts = re.split(r"(?=^### )", body, flags=re.MULTILINE)
    blocks: list[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part.startswith("###"):
            continue
        lines = part.splitlines()
        sub_title = lines[0].lstrip("#").strip()
        sub_body = _truncate_sub_body("\n".join(lines[1:]))
        blocks.append((sub_title, sub_body))
    return blocks


def parse_inheritance_md(path: Path, cfg: MigrateConfig, report: MigrateReport) -> list[MemoryDraft]:
    drafts: list[MemoryDraft] = []
    if not path.exists():
        return drafts

    text = path.read_text(encoding="utf-8")
    sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.splitlines()
        section_title = lines[0].lstrip("#").strip() if lines else "継承記憶"
        body = "\n".join(lines[1:]).strip()
        if not body and not section_title:
            continue

        h3_blocks = _split_h3_blocks(body)
        if h3_blocks:
            for sub_title, sub_body in h3_blocks:
                content = f"{sub_title}\n{sub_body}".strip() if sub_body else sub_title
                _add_inheritance_draft(
                    drafts,
                    report,
                    section_title=section_title,
                    content=content,
                    cfg=cfg,
                    metadata={"subsection": sub_title},
                )
        else:
            content = f"{section_title}\n{body}".strip() if body else section_title
            _add_inheritance_draft(
                drafts,
                report,
                section_title=section_title,
                content=content,
                cfg=cfg,
            )

    return drafts


def _extract_subjects_from_json(text: str) -> list[dict]:
    """壊れた JSON でも subject オブジェクトを拾う"""
    subjects: list[dict] = []
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "sessions" in data:
            for session in data["sessions"]:
                subjects.extend(session.get("subjects", []))
        if isinstance(data, dict) and "subjects" in data:
            subjects.extend(data["subjects"])
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "subjects" in item:
                    subjects.extend(item["subjects"])
        if subjects:
            return subjects
    except json.JSONDecodeError:
        pass

    # フォールバック: "summary": を含むオブジェクト塊を抽出
    pattern = re.compile(r'\{\s*"date"\s*:\s*"[^"]+"[\s\S]*?\}(?=\s*,|\s*\])', re.MULTILINE)
    for match in pattern.finditer(text):
        block = match.group(0)
        try:
            obj = json.loads(block)
            if "summary" in obj:
                subjects.append(obj)
        except json.JSONDecodeError:
            continue
    return subjects


def parse_memory_json(path: Path, cfg: MigrateConfig, report: MigrateReport) -> list[MemoryDraft]:
    drafts: list[MemoryDraft] = []
    if not path.exists():
        return drafts

    text = path.read_text(encoding="utf-8")
    subjects = _extract_subjects_from_json(text)

    field_map = {
        "summary": ("event", "要約"),
        "insights": ("knowledge", "洞察"),
        "evolutionMilestones": ("knowledge", "進化"),
        "relationshipWithMaster": ("relationship", "関係"),
        "emotionalNuance": ("relationship", "情緒"),
        "futureIntentions": ("knowledge", "将来"),
        "additionalNotes": ("relationship", "補足"),
    }

    for subject in subjects:
        base_meta = {
            "date": subject.get("date"),
            "sessionId": subject.get("sessionId"),
        }
        for key, (mem_type, label) in field_map.items():
            value = (subject.get(key) or "").strip()
            if not value:
                continue
            meta = {**base_meta, "field": label}
            _add_draft(
                drafts,
                report,
                type=mem_type,
                content=value,
                source=str(path.name),
                cfg=cfg,
                metadata=meta,
            )

    return drafts


def parse_diary(path: Path, cfg: MigrateConfig, report: MigrateReport) -> list[MemoryDraft]:
    drafts: list[MemoryDraft] = []
    if not path.exists():
        return drafts

    text = path.read_text(encoding="utf-8")
    date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    diary_date = date_match.group(0) if date_match else path.stem

    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("📖"):
            continue

        bullets = [ln.strip() for ln in para.splitlines() if ln.strip().startswith("-")]
        if bullets:
            for bullet in bullets:
                _add_draft(
                    drafts,
                    report,
                    type="event" if "マスター" in bullet else "relationship",
                    content=bullet.lstrip("- ").strip(),
                    source=path.name,
                    cfg=cfg,
                    metadata={"diary_date": diary_date},
                )
        elif len(para) > 20:
            _add_draft(
                drafts,
                report,
                type="event",
                content=para,
                source=path.name,
                cfg=cfg,
                metadata={"diary_date": diary_date},
            )

    return drafts


def load_non_canonical_drafts(
    legacy_dir: Path, cfg: MigrateConfig
) -> tuple[list[MemoryDraft], MigrateReport]:
    report = MigrateReport()
    drafts: list[MemoryDraft] = []
    drafts.extend(parse_memory_json(legacy_dir / "セリナの記憶.json", cfg, report))
    for rel in DIARY_FILES:
        drafts.extend(parse_diary(legacy_dir / rel, cfg, report))
    return drafts, report


def load_all_drafts(legacy_dir: Path, cfg: MigrateConfig) -> tuple[list[MemoryDraft], MigrateReport]:
    canonical, r1 = load_canonical_drafts(legacy_dir, cfg)
    others, r2 = load_non_canonical_drafts(legacy_dir, cfg)
    drafts = canonical + others
    report = MigrateReport()
    report.read_count = r1.read_count + r2.read_count
    report.pinned_count = sum(1 for d in drafts if d.pinned)
    report.by_source = dict(r1.by_source)
    for src, cnt in r2.by_source.items():
        report.by_source[src] = report.by_source.get(src, 0) + cnt
    return drafts, report


def load_canonical_drafts(
    legacy_dir: Path, cfg: MigrateConfig
) -> tuple[list[MemoryDraft], MigrateReport]:
    report = MigrateReport()
    drafts = parse_inheritance_md(legacy_dir / "継承記憶r1.md", cfg, report)
    return drafts, report


def run_migration(
    *,
    legacy_dir: Path,
    db_path: Path,
    dry_run: bool,
    cfg: MigrateConfig | None = None,
) -> MigrateReport:
    cfg = cfg or MigrateConfig()
    canonical_drafts, report_a = load_canonical_drafts(legacy_dir, cfg)
    other_drafts, report_b = load_non_canonical_drafts(legacy_dir, cfg)

    report = MigrateReport()
    report.read_count = report_a.read_count + report_b.read_count
    report.by_source = {**report_a.by_source}
    for src, cnt in report_b.by_source.items():
        report.by_source[src] = report.by_source.get(src, 0) + cnt

    if dry_run:
        report.pinned_count = sum(1 for d in canonical_drafts + other_drafts if d.pinned)
        report.merged_count = _estimate_dedup_count(other_drafts)
        report.saved_count = len(canonical_drafts) + len(other_drafts) - report.merged_count
        return report

    init_db(db_path)
    embedder = OllamaEmbedder()
    store = MemoryStore(embedder, db_path=db_path)

    merged = 0

    # 段階A: 正典を dedup なしで全件投入
    for draft in canonical_drafts:
        embedding = embedder.embed(draft.content)
        store.add_memory(
            type=draft.type,
            content=draft.content,
            importance=draft.importance,
            metadata=draft.metadata,
            source=draft.source,
            pinned=draft.pinned,
            embedding=embedding,
        )

    # 段階B: json・日記（正典への統合を禁止）
    for draft in other_drafts:
        embedding = embedder.embed(draft.content)
        similar = store.find_similar(embedding, cfg.dedup_similarity_threshold, limit=1)
        if similar:
            target_id, _, mem = similar[0]
            if mem.get("source") in cfg.canonical_sources:
                store.add_memory(
                    type=draft.type,
                    content=draft.content,
                    importance=draft.importance,
                    metadata=draft.metadata,
                    source=draft.source,
                    pinned=draft.pinned,
                    embedding=embedding,
                )
                continue
            store.merge_into(
                target_id,
                {
                    "content": draft.content,
                    "importance": draft.importance,
                    "metadata": draft.metadata,
                    "source": draft.source,
                    "pinned": draft.pinned,
                },
                source_priority=cfg.source_priority,
                canonical_sources=cfg.canonical_sources,
            )
            merged += 1
            continue

        store.add_memory(
            type=draft.type,
            content=draft.content,
            importance=draft.importance,
            metadata=draft.metadata,
            source=draft.source,
            pinned=draft.pinned,
            embedding=embedding,
        )

    report.saved_count = store.count_memories()
    report.merged_count = merged
    report.pinned_count = store.count_pinned()
    return report


def _estimate_dedup_count(drafts: list[MemoryDraft]) -> int:
    """dry-run 用の簡易重複見積もり（文字列類似）"""
    seen: list[str] = []
    dup = 0
    for draft in drafts:
        norm = re.sub(r"\s+", "", draft.content)[:120]
        if any(_jaccard(norm, s) > 0.75 for s in seen):
            dup += 1
        else:
            seen.append(norm)
    return dup


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def format_report(report: MigrateReport, dry_run: bool) -> str:
    mode = "【dry-run】" if dry_run else "【本実行】"
    lines = [
        mode,
        f"読み込み {report.read_count}件 → 統合後 {report.saved_count}件（重複{report.merged_count}件をまとめた）。固定記憶 {report.pinned_count}件。",
        "",
        "内訳（読み込み元）:",
    ]
    for source, count in sorted(report.by_source.items()):
        lines.append(f"  - {source}: {count}件")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serina 旧記憶の引っ越し")
    parser.add_argument("--legacy-dir", type=Path, default=DEFAULT_LEGACY_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true", help="DBに書かず件数だけ表示")
    args = parser.parse_args()

    report = run_migration(
        legacy_dir=args.legacy_dir,
        db_path=args.db,
        dry_run=args.dry_run,
    )
    print(format_report(report, args.dry_run))


if __name__ == "__main__":
    main()
