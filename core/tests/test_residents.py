from pathlib import Path

import pytest

from core.residents.service import ResidentError, ResidentService


ROOT_CONFIG = """
[core]
port = 8765
log_level = "INFO"

[world]
fps = 30
audio_volume = 100
voicevox_url = "http://127.0.0.1:50021"

[ecomode]
resume_delay_sec = 10

[residents]
enabled = []

[tasks]
allowed_dirs = ["runtime\\\\workspace"]
""".strip() + "\n"


def make_service(tmp_path: Path) -> ResidentService:
    (tmp_path / "config.toml").write_text(ROOT_CONFIG, encoding="utf-8")
    return ResidentService(tmp_path)


def test_create_resident_writes_templates_and_enables_it(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    resident = service.create(" Lapan ", "codex")

    assert resident.name == "Lapan"
    assert resident.brain == "codex"
    assert resident.brain_model is None
    assert resident.brain_reasoning_effort is None
    assert resident.avatar is None
    assert resident.tts.configured is False
    assert service.enabled_names == ("Lapan",)

    resident_dir = tmp_path / "residents" / "Lapan"
    persona = (resident_dir / "persona.md").read_text(encoding="utf-8")
    assert persona.startswith("# Lapan\n")
    for heading in ("## 性格", "## 口調", "## 日課", "## 得意", "## 決めごと"):
        assert heading in persona

    resident_config = (resident_dir / "config.toml").read_text(encoding="utf-8")
    assert 'brain = "codex"' in resident_config
    assert "avatar =" not in resident_config
    assert 'provider = "voicevox"' in resident_config

    root_config = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'enabled = ["Lapan"]' in root_config


def test_allows_more_than_three_enabled_residents(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Lapan", "codex")
    service.create("Kina", "codex")
    service.create("Shiro", "codex")
    fourth = service.create("Holo", "codex")
    fifth = service.create("Yuna", "codex")

    assert fourth.name == "Holo"
    assert fifth.name == "Yuna"
    assert service.enabled_names == ("Lapan", "Kina", "Shiro", "Holo", "Yuna")
    assert (tmp_path / "residents" / "Holo").exists()
    assert (tmp_path / "residents" / "Yuna").exists()


def test_holo_addon_brain_kind_is_special_and_limited_to_one_resident(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(ResidentError, match="Codex"):
        service.create("Another", "holo-addon", None, "high")

    holo = service.create("Holo", "holo-addon", "meaningless-model")
    assert holo.brain == "holo-addon"
    # The Holo mind is the ChatGPT Web conversation: model must stay empty.
    assert holo.brain_model is None
    assert holo.brain_reasoning_effort is None

    with pytest.raises(ResidentError, match="1人だけ"):
        service.create("Holo2", "holo-addon")

    kina = service.create("Kina", "codex")
    assert kina.brain == "codex"
    with pytest.raises(ResidentError, match="1人だけ"):
        service.set_brain("Kina", "holo-addon")

    # Re-saving the same Holo resident must not trip the singleton guard.
    updated = service.set_brain("Holo", "holo-addon")
    assert updated.brain == "holo-addon"
    assert updated.brain_model is None

    # Moving Holo to a normal driver frees the single holo-addon slot.
    service.set_brain("Holo", "codex")
    switched = service.set_brain("Kina", "holo-addon")
    assert switched.brain == "holo-addon"


def test_holo_addon_resident_persists_and_reloads(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Holo", "holo-addon")

    # Restart path: Core re-reads [residents].enabled from config.toml.
    import tomllib

    enabled = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["residents"]["enabled"]
    reloaded = ResidentService(tmp_path, tuple(enabled))
    definition = reloaded.load("Holo")
    assert definition.brain == "holo-addon"
    assert reloaded.enabled_names == ("Holo",)


def test_reorder_residents_persists_sidebar_order(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Cursor", "cursor")
    service.create("Gemini", "gemini")
    service.create("Codex", "codex")

    reordered = service.reorder(["Cursor", "Codex", "Gemini"])

    assert reordered == ("Cursor", "Codex", "Gemini")
    assert service.enabled_names == reordered
    root_config = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'enabled = ["Cursor", "Codex", "Gemini"]' in root_config

    with pytest.raises(ResidentError, match="重複"):
        service.reorder(["Cursor", "Cursor", "Gemini"])
    with pytest.raises(ResidentError, match="一致しません"):
        service.reorder(["Cursor", "Codex"])


def test_recreated_lapan_reuses_existing_initial_avatar(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    avatar = tmp_path / "avatars" / "lapan" / "lapan.vrm"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"vrm")

    resident = service.create("Lapan", "codex")

    assert resident.brain == "codex"
    assert resident.brain_model is None
    assert resident.brain_reasoning_effort is None
    assert resident.avatar == "lapan/lapan.vrm"


def test_set_brain_persists_selected_provider_and_model(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Kina", "codex")

    updated = service.set_brain("Kina", "cursor", "cursor-grok-4.6-high")

    assert updated.brain == "cursor"
    assert updated.brain_model == "cursor-grok-4.6-high"
    config = (tmp_path / "residents" / "Kina" / "config.toml").read_text(encoding="utf-8")
    assert 'brain = "cursor"' in config
    assert 'brain_model = "cursor-grok-4.6-high"' in config

    codex = service.set_brain("Kina", "codex", "gpt-5.6-sol", "xhigh")
    assert codex.brain == "codex"
    assert codex.brain_model == "gpt-5.6-sol"
    assert codex.brain_reasoning_effort == "xhigh"
    config = (tmp_path / "residents" / "Kina" / "config.toml").read_text(encoding="utf-8")
    assert 'brain_model = "gpt-5.6-sol"' in config
    assert 'brain_reasoning_effort = "xhigh"' in config

    reset = service.set_brain("Kina", "codex", None, None)
    assert reset.brain == "codex"
    assert reset.brain_model is None
    assert reset.brain_reasoning_effort is None
    config = (tmp_path / "residents" / "Kina" / "config.toml").read_text(encoding="utf-8")
    assert "brain_model =" not in config
    assert "brain_reasoning_effort =" not in config


def test_reasoning_effort_is_codex_only_and_validated(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Kina", "codex")

    with pytest.raises(ResidentError, match="Codexでのみ"):
        service.set_brain("Kina", "cursor", "cursor-grok-4.6-high", "high")
    with pytest.raises(ResidentError, match="推論強度が不正"):
        service.set_brain("Kina", "codex", "gpt-5.6-sol", "extreme")


def test_resident_name_rejects_windows_invalid_reserved_and_duplicate_names(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Lapan", "codex")

    for invalid in ("", "bad/name", "bad:name", "CON", "com1.txt", "name."):
        with pytest.raises(ResidentError):
            service.create(invalid, "codex")

    with pytest.raises(ResidentError, match="既に存在"):
        service.create("lapan", "codex")

    gemini = service.create("Kina", "gemini", "gemini-flash-latest")
    assert gemini.brain == "gemini"
    assert gemini.brain_model == "gemini-flash-latest"


def test_delete_resident_requires_exact_confirmation_and_keeps_avatar_file(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Lapan", "codex")
    avatar = tmp_path / "avatars" / "lapan" / "lapan.vrm"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"vrm")
    private_dir = tmp_path / "residents" / "Lapan" / "private"
    private_dir.mkdir()
    (private_dir / "whispers.jsonl").write_text("secret\n", encoding="utf-8")

    with pytest.raises(ResidentError):
        service.delete("Lapan", "delete")

    service.delete("Lapan", "Delete")

    assert not (tmp_path / "residents" / "Lapan").exists()
    assert avatar.exists()
    assert service.enabled_names == ()
    assert 'enabled = []' in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_set_tts_persists_voicevox_configuration(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Lapan", "codex")

    updated = service.set_tts("Lapan", {
        "enabled": True,
        "provider": "voicevox",
        "speaker_uuid": "speaker-1",
        "style_id": 3,
        "speed": 1.1,
        "pitch": 0.05,
        "intonation": 0.9,
    })

    assert updated.tts.configured is True
    assert updated.tts.speaker_uuid == "speaker-1"
    assert updated.tts.style_id == 3
    config_text = (tmp_path / "residents" / "Lapan" / "config.toml").read_text(encoding="utf-8")
    assert 'speaker_uuid = "speaker-1"' in config_text
    assert "style_id = 3" in config_text


def test_set_avatar_validates_root_and_persists_relative_vrm_path(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.create("Lapan", "codex")
    avatar = tmp_path / "avatars" / "lapan" / "lapan.vrm"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"vrm")

    updated = service.set_avatar("Lapan", "lapan\\lapan.vrm")

    assert updated.avatar == "lapan/lapan.vrm"
    config_text = (tmp_path / "residents" / "Lapan" / "config.toml").read_text(encoding="utf-8")
    assert 'avatar = "lapan/lapan.vrm"' in config_text

    for invalid in ("../outside.vrm", "lapan/avatar.glb", "missing.vrm"):
        with pytest.raises(ResidentError):
            service.set_avatar("Lapan", invalid)


def test_load_resident_reads_brain_avatar_and_voice_configuration(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    resident_dir = tmp_path / "residents" / "Lapan"
    resident_dir.mkdir(parents=True)
    (resident_dir / "persona.md").write_text("# Lapan\n", encoding="utf-8")
    (resident_dir / "config.toml").write_text(
        """
brain = "codex"
brain_model = "gpt-5.6-sol"
brain_reasoning_effort = "high"
avatar = "lapan/lapan.vrm"
tick_interval_min = 30
spawn_location = "center"

[tts]
enabled = true
provider = "voicevox"
speaker_uuid = "speaker"
style_id = 3
speed = 1.1
pitch = 0.0
intonation = 0.9
""".strip() + "\n",
        encoding="utf-8",
    )
    service = ResidentService(tmp_path, ("Lapan",))

    resident = service.list_enabled()[0]

    assert resident.brain == "codex"
    assert resident.brain_model == "gpt-5.6-sol"
    assert resident.brain_reasoning_effort == "high"
    assert resident.avatar == "lapan/lapan.vrm"
    assert resident.tts.configured is True
    assert resident.tts.style_id == 3
