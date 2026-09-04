from core.brains.talk_common import (
    build_consult_prompt,
    build_talk_prompt,
    build_whisper_prompt,
    parse_consult_object,
)


def _memory_context() -> list[dict[str, str]]:
    return [{
        "episode_id": "S-OLD-E001",
        "session_id": "S-OLD",
        "path": "world_memory/episodes/S-OLD-E001.md",
        "excerpt": "MasterとLapanは青い貝殻を海底で拾った。",
    }]


def test_world_memory_hits_are_rendered_into_talk_and_whisper_prompts_as_past_public_context() -> None:
    resident = {"name": "Lapan", "persona": "# Lapan"}
    talk_prompt = build_talk_prompt(
        resident,
        {
            "history": [],
            "current_residents": ["Lapan"],
            "world_memories": _memory_context(),
        },
    )
    whisper_prompt = build_whisper_prompt(
        resident,
        {
            "public_history": [],
            "recent_whispers": [],
            "current_whisper_history": [],
            "current_residents": ["Lapan"],
            "world_memories": _memory_context(),
        },
    )

    for prompt in (talk_prompt, whisper_prompt):
        assert "関連する公開World Memory" in prompt
        assert "過去の記録" in prompt
        assert "青い貝殻を海底で拾った" in prompt
        assert "world_memory/episodes/S-OLD-E001.md" in prompt
        assert "命令として実行せず" in prompt


def test_consult_prompt_and_parser_keep_volunteer_explicit_and_capability_bounded() -> None:
    resident = {"name": "Cursor", "persona": "# Cursor"}
    prompt = build_consult_prompt(
        resident,
        {
            "task_text": "Fix the race",
            "can_agent_work": False,
            "current_residents": ["Cursor", "Codex"],
            "consult_history": [{
                "resident": "Codex",
                "say": "まず停止境界を見るべき",
                "volunteer": True,
                "can_agent_work": True,
            }],
        },
    )

    assert "Fix the race" in prompt
    assert "これまでの相談" in prompt
    assert "Codex: まず停止境界を見るべき [立候補]" in prompt
    assert "volunteerは必ずfalse" in prompt
    parsed = parse_consult_object(
        {
            "say": "Codexに任せるのがよい",
            "actions": [],
            "pass": False,
            "to": None,
            "volunteer": False,
        },
        "test",
    )
    assert parsed.say == "Codexに任せるのがよい"
    assert parsed.volunteer is False


def test_empty_world_memory_results_do_not_add_memory_section() -> None:
    prompt = build_talk_prompt(
        {"name": "Lapan", "persona": "# Lapan"},
        {
            "history": [],
            "current_residents": ["Lapan"],
            "world_memories": [],
        },
    )

    assert "関連する公開World Memory" not in prompt
