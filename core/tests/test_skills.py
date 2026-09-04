import asyncio
from pathlib import Path

from websockets.asyncio.client import connect

from core.brains.base import BrainResponse
from core.brains.talk_common import build_talk_prompt, build_whisper_prompt
from core.config import load_config
from core.protocol import make_message, parse_message
from core.server import CoreServer
from core.skills import MAX_SKILL_BYTES, MAX_TOTAL_SKILL_BYTES, SkillRegistry


def _write_skill(root: Path, name: str, description: str, body: str) -> Path:
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_skill_with_exact_size(root: Path, name: str, size: int) -> Path:
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    prefix = f"---\nname: {name}\ndescription: boundary test\n---\n\n"
    suffix = "\n"
    filler_bytes = size - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    assert filler_bytes > 0
    path.write_bytes((prefix + ("x" * filler_bytes) + suffix).encode("utf-8"))
    assert path.stat().st_size == size
    return path


def test_empty_skill_registry_changes_no_prompt(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")

    assert registry.load() == ()
    assert registry.prompt_context() == ""
    assert registry.public_payload() == {"count": 0, "skills": []}

    talk = build_talk_prompt(
        {"name": "Lapan", "persona": "静かに話す。"},
        {"history": [], "current_residents": ["Lapan"], "skills": registry.prompt_context()},
    )
    whisper = build_whisper_prompt(
        {"name": "Lapan", "persona": "静かに話す。"},
        {"current_residents": ["Lapan"], "skills": registry.prompt_context()},
    )
    assert "Nirai Skills" not in talk
    assert "Nirai Skills" not in whisper


def test_valid_skill_is_loaded_on_demand_and_injected_into_brain_prompts(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    assert registry.load() == ()

    _write_skill(
        tmp_path,
        "sample-review",
        "レビュー時に境界条件を確認する。",
        "# Sample Review\n境界条件を確認してから完了する。",
    )

    skills = registry.load()
    assert len(skills) == 1
    assert skills[0].name == "sample-review"
    assert skills[0].description == "レビュー時に境界条件を確認する。"
    assert "境界条件を確認してから完了する。" in skills[0].body

    context = registry.prompt_context()
    assert "<nirai-skill name='sample-review'>" in context
    assert "境界条件を確認してから完了する。" in context

    talk = build_talk_prompt(
        {"name": "Lapan", "persona": "静かに話す。"},
        {"history": [], "current_residents": ["Lapan"], "skills": context},
    )
    whisper = build_whisper_prompt(
        {"name": "Lapan", "persona": "静かに話す。"},
        {"current_residents": ["Lapan"], "skills": context},
    )
    assert "Nirai Skills" in talk
    assert "sample-review" in talk
    assert "Nirai Skills" in whisper
    assert "sample-review" in whisper


def test_core_passes_registry_skills_to_normal_brain_calls(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        """
[core]
port = 8765
log_level = "INFO"

[world]
fps = 30
audio_volume = 0
voicevox_url = "http://127.0.0.1:50021"

[ecomode]
resume_delay_sec = 10

[residents]
enabled = ["Lapan"]

[tasks]
allowed_dirs = ["runtime\\\\workspace"]
""".strip(),
        encoding="utf-8",
    )
    resident_dir = tmp_path / "residents" / "Lapan"
    resident_dir.mkdir(parents=True)
    (resident_dir / "persona.md").write_text("静かに話す。", encoding="utf-8")
    (resident_dir / "config.toml").write_text(
        'brain = "codex"\navatar = ""\nspawn_location = "center"\n',
        encoding="utf-8",
    )
    _write_skill(tmp_path, "sample-core", "Core配線確認。", "# Core\nBrainへ届く。")

    class CaptureBrain:
        def __init__(self) -> None:
            self.contexts: list[dict[str, object]] = []

        async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
            self.contexts.append(dict(context))
            return BrainResponse(say="確認", actions=(), passed=False)

        async def cancel(self, invocation_id: str) -> bool:
            return True

    async def scenario() -> None:
        brain = CaptureBrain()
        server = CoreServer(load_config(tmp_path), port_override=0, brain_driver=brain)
        await server.start()
        try:
            assert server.bound_port is not None
            async with connect(f"ws://127.0.0.1:{server.bound_port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world", "secret": server._world_secret}, "hello"))
                await websocket.recv()
                await websocket.send(make_message(
                    "master_say",
                    {"text": "確認して", "request_id": "REQ-SKILL"},
                ))
                while True:
                    message = parse_message(await asyncio.wait_for(websocket.recv(), timeout=1.0))
                    if (
                        message["type"] == "response_state"
                        and message["payload"].get("request_id") == "REQ-SKILL"
                        and message["payload"].get("active") is False
                    ):
                        break
        finally:
            await server.stop()

        assert len(brain.contexts) == 1
        assert "sample-core" in str(brain.contexts[0]["skills"])
        assert "Brainへ届く。" in str(brain.contexts[0]["skills"])

    asyncio.run(scenario())


def test_total_limit_skips_only_the_overflowing_skill_and_checks_later_skills(
    tmp_path: Path,
) -> None:
    _write_skill_with_exact_size(tmp_path, "a-base", MAX_SKILL_BYTES)
    _write_skill_with_exact_size(tmp_path, "b-base", MAX_SKILL_BYTES - (4 * 1024))
    _write_skill_with_exact_size(tmp_path, "c-overflow", 8 * 1024)
    _write_skill_with_exact_size(tmp_path, "d-fits", 4 * 1024)

    registry = SkillRegistry(tmp_path / "skills")
    loaded = registry.load()

    assert [skill.name for skill in loaded] == ["a-base", "b-base", "d-fits"]
    assert sum(
        (tmp_path / "skills" / skill.name / "SKILL.md").stat().st_size
        for skill in loaded
    ) == MAX_TOTAL_SKILL_BYTES


def test_invalid_skill_is_skipped_without_hiding_valid_sibling(tmp_path: Path) -> None:
    _write_skill(tmp_path, "valid", "有効なSkill。", "# Valid\n使える。")
    invalid = tmp_path / "skills" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: 不正。\n---\n\n# Invalid\n使わない。\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(tmp_path / "skills")
    loaded = registry.load()

    assert [skill.name for skill in loaded] == ["valid"]
    payload = registry.public_payload()
    assert payload["count"] == 1
    assert payload["skills"][0]["name"] == "valid"
