from pathlib import Path

import pytest

from core.config import ConfigError, load_config, save_audio_volume


VALID_CONFIG = """
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
""".strip()


def write_config(root: Path, content: str = VALID_CONFIG) -> None:
    (root / "config.toml").write_text(content, encoding="utf-8")


def test_load_config_reads_expected_values(tmp_path: Path) -> None:
    write_config(tmp_path)

    config = load_config(tmp_path)

    assert config.root == tmp_path.resolve()
    assert config.core.port == 8765
    assert config.core.log_level == "INFO"
    assert config.world.audio_volume == 100
    assert config.world.voicevox_url == "http://127.0.0.1:50021"
    assert config.memory.world_processor == "disabled"
    assert config.memory.extraction_model == "gemini-3.5-flash-lite"
    assert config.memory.embedding_model == "gemini-embedding-2"
    assert config.memory.embedding_dim == 768
    assert config.memory.background_interval_sec == 60
    assert config.memory.background_daily_limit == 400
    assert config.memory.query_embedding_daily_limit == 500
    assert config.memory.embedding_daily_budget == 900
    assert config.memory.private_semantic_provider == "disabled"
    assert config.memory.private_background_interval_sec == 60
    assert config.residents_enabled == ()
    assert config.tasks_allowed_dirs == ("runtime\\workspace",)


def test_missing_config_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config.toml not found"):
        load_config(tmp_path)


def test_save_audio_volume_updates_world_setting(tmp_path: Path) -> None:
    write_config(tmp_path)

    save_audio_volume(tmp_path, 35)

    assert load_config(tmp_path).world.audio_volume == 35


def test_load_config_reads_explicit_gemini_memory_settings(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        VALID_CONFIG
        + "\n\n[memory]\n"
        + 'world_processor = "gemini"\n'
        + 'extraction_model = "gemini-3.5-flash-lite"\n'
        + 'embedding_model = "gemini-embedding-2"\n'
        + "embedding_dim = 768\n"
        + "background_interval_sec = 120\n"
        + "background_daily_limit = 300\n"
        + "query_embedding_daily_limit = 400\n"
        + "embedding_daily_budget = 800\n"
        + 'private_semantic_provider = "gemini"\n'
        + "private_background_interval_sec = 7\n",
    )

    config = load_config(tmp_path)

    assert config.memory.world_processor == "gemini"
    assert config.memory.background_interval_sec == 120
    assert config.memory.background_daily_limit == 300
    assert config.memory.query_embedding_daily_limit == 400
    assert config.memory.embedding_daily_budget == 800
    assert config.memory.private_semantic_provider == "gemini"
    assert config.memory.private_background_interval_sec == 7


def test_invalid_memory_processor_is_rejected(tmp_path: Path) -> None:
    write_config(tmp_path, VALID_CONFIG + '\n\n[memory]\nworld_processor = "local-llm"\n')

    with pytest.raises(ConfigError, match="memory.world_processor"):
        load_config(tmp_path)


def test_memory_embedding_limits_must_fit_shared_daily_budget(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        VALID_CONFIG
        + "\n\n[memory]\n"
        + "background_daily_limit = 500\n"
        + "query_embedding_daily_limit = 500\n"
        + "embedding_daily_budget = 900\n",
    )

    with pytest.raises(ConfigError, match="fit within embedding_daily_budget"):
        load_config(tmp_path)


def test_invalid_port_is_rejected(tmp_path: Path) -> None:
    write_config(tmp_path, VALID_CONFIG.replace("port = 8765", "port = 70000"))

    with pytest.raises(ConfigError, match="core.port"):
        load_config(tmp_path)
