"""Offline model and capacity checks before importing any AI model.

All checks fail closed. This module imports neither torch nor Kimodo and can
be tested without a GPU, model weights or network access.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

MODEL_NAME = "Kimodo-SOMA-RP-v1.1"
MNTP_VIEW = "LLM2Vec-Meta-Llama-3-8B-Instruct-mntp"
TEXT_REVISIONS = {
    "base": ("meta-llama/Meta-Llama-3-8B-Instruct", "8afb486c1db24fe5011ec46dfbe5b5dccdb575c2"),
    "mntp": ("McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp", "31474e395ada192e8ed1586db6be79fb3b70c9c0"),
    "supervised": ("McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised", "baa8ebf04a1c2500e61288e7dad65e8ae42601a7"),
}
MIN_AVAILABLE_RAM_MIB = 16 * 1024  # LLM2Vec observed 14.4 GiB; keep some headroom.
MIN_FREE_VRAM_MIB = 3072  # Kimodo observed ~2.1 GiB; reserve extra headroom.
MIN_CPU_TRIAL_RAM_MIB = 4 * 1024  # CPU Kimodo with cached features observed ~1.7 GiB; the brain stays loaded.


def _weights(path: Path) -> bool:
    indices = list(path.glob("*.index.json"))
    if indices:
        for index in indices:
            try:
                shards = set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values())
            except (ValueError, KeyError, TypeError):
                return False
            if not shards or any(not (path / shard).is_file() for shard in shards):
                return False
        return True
    return any(path.glob("*.safetensors")) or any(path.glob("pytorch_model*.bin"))


def local_models(poc: Path, hf_home: Path, checkpoints: Path) -> dict[str, Path]:
    """Ensure all approved local weights exist before model imports."""
    if not (poc / "vendor" / "kimodo" / "kimodo" / "__init__.py").is_file():
        raise RuntimeError("Local Kimodo source unavailable")
    paths = {}
    for key, (repo, revision) in TEXT_REVISIONS.items():
        path = (hf_home / "local-views" / MNTP_VIEW / revision if key == "mntp" else
                hf_home / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots" / revision)
        if not path.is_dir():
            raise RuntimeError(f"Local text snapshot unavailable: {key}")
        if key == "base":
            valid = (path / "config.json").is_file() and _weights(path)
        else:
            valid = (path / "adapter_config.json").is_file() and (path / "adapter_model.safetensors").is_file()
        if valid and key == "mntp":
            # The pinned adapter must resolve its base to the approved local
            # snapshot. The original Hub adapter points to a repo name and
            # transformers attempts a second lookup under refs/main offline.
            try:
                name = json.loads((path / "adapter_config.json").read_text(encoding="utf-8"))["base_model_name_or_path"]
                valid = isinstance(name, str) and Path(name).resolve() == paths["base"].resolve()
            except (OSError, ValueError, KeyError, TypeError):
                valid = False
        if not valid:
            raise RuntimeError(f"Local text model incomplete: {key}")
        paths[key] = path
    paths["motion"] = local_motion_checkpoint(checkpoints)
    return paths


def local_motion_checkpoint(checkpoints: Path) -> Path:
    """Check only the motion checkpoint for cached-feature, CPU-only trials."""
    motion = checkpoints / MODEL_NAME
    if not (motion / "config.yaml").is_file() or not _weights(motion):
        raise RuntimeError("Local Kimodo checkpoint incomplete")
    return motion


def available_ram_mib() -> int:
    if os.name != "nt":
        import os as posix_os
        return (posix_os.sysconf("SC_AVPHYS_PAGES") * posix_os.sysconf("SC_PAGE_SIZE")) // (1024 * 1024)

    class Status(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
            ("available_extended", ctypes.c_ulonglong),
        ]

    status = Status()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise RuntimeError("Available RAM could not be checked")
    return status.available_physical // (1024 * 1024)


def available_vram_mib() -> int:
    executable = shutil.which("nvidia-smi") or (
        "C:/Windows/System32/nvidia-smi.exe" if os.name == "nt" else "nvidia-smi"
    )
    try:
        result = subprocess.run(
            [executable, "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Available GPU VRAM could not be checked") from None


def require_capacity(ram_mib: int, vram_mib: int) -> None:
    if ram_mib < MIN_AVAILABLE_RAM_MIB:
        raise RuntimeError(f"Insufficient RAM ({ram_mib} MiB available; need {MIN_AVAILABLE_RAM_MIB})")
    if vram_mib < MIN_FREE_VRAM_MIB:
        raise RuntimeError(f"Insufficient GPU VRAM ({vram_mib} MiB available; need {MIN_FREE_VRAM_MIB})")


def offline_only(poc: Path, hf_home: Path, checkpoints: Path) -> None:
    """Pin all local caches and refuse outgoing sockets, including local HTTP."""
    os.environ.update({
        "HF_HOME": str(hf_home), "HF_HUB_CACHE": str(hf_home / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(hf_home / "hub"),
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false",
        "TEXT_ENCODER_MODE": "local", "TEXT_ENCODER_DEVICE": "cpu",
        "LOCAL_CACHE": "True", "CHECKPOINT_DIR": str(checkpoints),
    })
    os.environ.pop("TEXT_ENCODERS_DIR", None)
    vendor = str(poc / "vendor" / "kimodo")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)

    def forbidden(*_args, **_kwargs):
        raise RuntimeError("Outbound connections disabled")

    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden


def pin_text_models(paths: dict[str, Path], hf_home: Path) -> None:
    """Allow the approved three pinned model revisions only."""
    import huggingface_hub
    from huggingface_hub import file_download
    original_file = huggingface_hub.hf_hub_download
    original_snapshot = huggingface_hub.snapshot_download
    pins = {repo: revision for repo, revision in TEXT_REVISIONS.values()}

    def file(repo_id, *args, **kwargs):
        if repo_id not in pins:
            raise RuntimeError("Model is not on the offline allowlist")
        kwargs.update(revision=pins[repo_id], cache_dir=str(hf_home / "hub"), local_files_only=True)
        return original_file(repo_id, *args, **kwargs)

    def snapshot(repo_id, *args, **kwargs):
        if repo_id not in pins:
            raise RuntimeError("Model is not on the offline allowlist")
        kwargs.update(revision=pins[repo_id], cache_dir=str(hf_home / "hub"), local_files_only=True)
        return original_snapshot(repo_id, *args, **kwargs)

    huggingface_hub.hf_hub_download = file
    huggingface_hub.snapshot_download = snapshot
    file_download.hf_hub_download = file
