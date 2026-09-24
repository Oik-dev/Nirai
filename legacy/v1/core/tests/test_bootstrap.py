from __future__ import annotations

from pathlib import Path

import nirai_bootstrap
from core import provider_runtime


def _prepared_root(tmp_path: Path) -> Path:
    root = tmp_path / "Nirai"
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    electron = root / "world" / "node_modules" / "electron" / "dist" / "electron.exe"
    electron.parent.mkdir(parents=True)
    electron.write_bytes(b"")
    bundle = root / "world" / "out" / "main" / "index.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("// built\n", encoding="utf-8")
    (root / "config.toml").write_text(
        "[core]\nport=8765\nlog_level='INFO'\n"
        "[world]\nfps=60\naudio_volume=100\nvoicevox_url='http://127.0.0.1:50021'\n"
        "[ecomode]\nresume_delay_sec=0\n"
        "[residents]\nenabled=[]\n"
        "[tasks]\nallowed_dirs=[]\n",
        encoding="utf-8",
    )
    return root


def test_preflight_treats_optional_providers_as_nonfatal(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(nirai_bootstrap.shutil, "which", lambda _name: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_API_KEY", raising=False)

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    assert not [result for result in results if result.fatal and result.status == "ERROR"]
    optional = [result for result in results if result.name.startswith("optional-provider:")]
    assert optional
    assert all(result.fatal is False for result in optional)


def test_preflight_cursor_requires_resolvable_runtime_not_launcher_alone(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(nirai_bootstrap.shutil, "which", lambda _name: None)
    local_app_data = tmp_path / "LocalAppData"
    install_dir = local_app_data / "cursor-agent"
    install_dir.mkdir(parents=True)
    (install_dir / "agent.cmd").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    missing_runtime = nirai_bootstrap.run_preflight(root, world_dev=False)
    cursor = next(result for result in missing_runtime if result.name == "optional-provider:cursor")
    assert cursor.status == "WARN"

    runtime = install_dir / "versions" / "2026.09.02-c22c1a3"
    runtime.mkdir(parents=True)
    (runtime / "node.exe").write_bytes(b"")
    (runtime / "index.js").write_text("// runtime\n", encoding="utf-8")
    complete = nirai_bootstrap.run_preflight(root, world_dev=False)
    cursor = next(result for result in complete if result.name == "optional-provider:cursor")
    assert cursor.status == "OK"
    assert "index.js" in cursor.detail


def test_preflight_codex_cmd_requires_node_and_codex_js(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    bin_dir = tmp_path / "codex-bin"
    bin_dir.mkdir()
    launcher = bin_dir / "codex.cmd"
    launcher.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty-local-app-data"))

    monkeypatch.setattr(
        provider_runtime.shutil,
        "which",
        lambda name: str(launcher) if name == "codex.cmd" else None,
    )
    missing_runtime = nirai_bootstrap.run_preflight(root, world_dev=False)
    codex = next(result for result in missing_runtime if result.name == "optional-provider:codex")
    assert codex.status == "WARN"

    node = tmp_path / "node.exe"
    node.write_bytes(b"MZ")
    codex_js = bin_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    codex_js.parent.mkdir(parents=True)
    codex_js.write_text("// codex\n", encoding="utf-8")
    monkeypatch.setattr(
        provider_runtime.shutil,
        "which",
        lambda name: str(launcher) if name == "codex.cmd" else (str(node) if name == "node.exe" else None),
    )
    complete = nirai_bootstrap.run_preflight(root, world_dev=False)
    codex = next(result for result in complete if result.name == "optional-provider:codex")
    assert codex.status == "OK"
    assert "codex.js" in codex.detail


def test_preflight_falls_back_to_codex_desktop_when_path_launcher_is_stale(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    stale_dir = tmp_path / "stale-bin"
    stale_dir.mkdir()
    stale = stale_dir / "codex.cmd"
    stale.write_text("@echo off\n", encoding="utf-8")
    local_app_data = tmp_path / "LocalAppData"
    managed = local_app_data / "OpenAI" / "Codex" / "bin" / "abc123" / "codex.exe"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(
        provider_runtime.shutil,
        "which",
        lambda name: str(stale) if name == "codex.cmd" else None,
    )

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    codex = next(result for result in results if result.name == "optional-provider:codex")
    assert codex.status == "OK"
    assert codex.detail == str(managed)


def test_preflight_detects_codex_desktop_managed_runtime(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(nirai_bootstrap.shutil, "which", lambda _name: None)
    monkeypatch.setattr(provider_runtime.shutil, "which", lambda _name: None)
    local_app_data = tmp_path / "LocalAppData"
    managed = local_app_data / "OpenAI" / "Codex" / "bin" / "abc123" / "codex.exe"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    codex = next(result for result in results if result.name == "optional-provider:codex")
    assert codex.status == "OK"
    assert codex.detail == str(managed)


def test_preflight_rejects_zero_byte_codex_desktop_runtime(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(nirai_bootstrap.shutil, "which", lambda _name: None)
    monkeypatch.setattr(provider_runtime.shutil, "which", lambda _name: None)
    local_app_data = tmp_path / "LocalAppData"
    managed = local_app_data / "OpenAI" / "Codex" / "bin" / "broken" / "codex.exe"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    codex = next(result for result in results if result.name == "optional-provider:codex")
    assert codex.status == "WARN"


def test_preflight_reports_gemini_using_the_same_world_env_as_core(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(nirai_bootstrap.shutil, "which", lambda _name: None)
    (root / "world" / ".env").write_text("GEMINI_API_KEY=test-secret\n", encoding="utf-8")

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    gemini = next(result for result in results if result.name == "optional-provider:gemini")
    assert gemini.status == "OK"
    assert gemini.detail.endswith("world\\.env") or gemini.detail.endswith("world/.env")


def test_preflight_does_not_claim_claude_is_usable_from_a_cli_path_alone(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(nirai_bootstrap.shutil, "which", lambda name: "C:/fake/claude.exe" if name == "claude.exe" else None)

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    claude = next(result for result in results if result.name == "optional-provider:claude")
    assert claude.status == "WARN"
    assert "disabled" in claude.detail


def test_preflight_rejects_config_that_product_loader_would_reject(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    (root / "config.toml").write_text(
        "[core]\nport='8765'\nlog_level='INFO'\n"
        "[world]\nfps=60\naudio_volume=100\nvoicevox_url='http://127.0.0.1:50021'\n"
        "[ecomode]\nresume_delay_sec=0\n"
        "[residents]\nenabled=[]\n"
        "[tasks]\nallowed_dirs=[]\n",
        encoding="utf-8",
    )

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    config = next(result for result in results if result.name == "config")
    assert config.status == "ERROR"
    assert config.fatal is True
    assert "core.port" in config.detail


def test_preflight_fails_closed_when_runtime_package_is_missing(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    original_find_spec = nirai_bootstrap.importlib.util.find_spec
    monkeypatch.setattr(
        nirai_bootstrap.importlib.util,
        "find_spec",
        lambda name: None if name == "sqlite_vec" else original_find_spec(name),
    )

    results = nirai_bootstrap.run_preflight(root, world_dev=False)

    sqlite_check = next(result for result in results if result.name == "python-package:sqlite-vec")
    assert sqlite_check.status == "ERROR"
    assert sqlite_check.fatal is True


def test_preflight_requires_production_world_bundle_only_in_production(tmp_path: Path, monkeypatch) -> None:
    root = _prepared_root(tmp_path)
    monkeypatch.setattr(nirai_bootstrap.sys, "executable", str(root / ".venv" / "Scripts" / "python.exe"))
    (root / "world" / "out" / "main" / "index.js").unlink()
    dev_entry = root / "world" / "node_modules" / ".bin" / "electron-vite.cmd"
    dev_entry.parent.mkdir(parents=True, exist_ok=True)
    dev_entry.write_text("@echo off\n", encoding="utf-8")

    production = nirai_bootstrap.run_preflight(root, world_dev=False)
    development = nirai_bootstrap.run_preflight(root, world_dev=True)

    production_bundle = next(result for result in production if result.name == "world-production-build")
    assert production_bundle.status == "ERROR"
    assert production_bundle.fatal is True
    assert not [result for result in development if result.fatal and result.status == "ERROR"]
