from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import tomllib
from typing import Any, Sequence

from .base import BrainError, BrainResponse, BrainResponseError, BrainUnavailableError
from .process_manager import CompletedInvocation, ProcessManager
from .talk_common import (
    build_consult_prompt,
    build_talk_prompt,
    build_whisper_prompt,
    extract_consult_result_envelope,
)


CODEX_TIMEOUT_SEC = 120.0
CODEX_REASONING_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra High",
    "ultra": "Ultra",
    "max": "Max",
}
LOGGER = logging.getLogger("nirai.core.brain.codex")


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def load_codex_defaults() -> tuple[str | None, str | None]:
    path = _codex_home() / "config.toml"
    if not path.is_file():
        return None, None
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None, None
    model = raw.get("model")
    reasoning = raw.get("model_reasoning_effort")
    return (
        model.strip() if isinstance(model, str) and model.strip() else None,
        reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None,
    )


def list_codex_models() -> list[dict[str, Any]]:
    path = _codex_home() / "models_cache.json"
    if not path.is_file():
        raise BrainUnavailableError("Codex model cache was not found")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainUnavailableError(f"Codex model cache could not be read: {exc}") from exc

    candidates = raw if isinstance(raw, list) else raw.get("models", []) if isinstance(raw, dict) else []
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("visibility") not in {None, "list"}:
            continue
        model_id = candidate.get("slug")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        display_name = candidate.get("display_name")
        raw_efforts = candidate.get("supported_reasoning_levels")
        reasoning_efforts: list[dict[str, str]] = []
        if isinstance(raw_efforts, list):
            for raw_effort in raw_efforts:
                if not isinstance(raw_effort, dict):
                    continue
                effort = raw_effort.get("effort")
                if not isinstance(effort, str) or not effort.strip():
                    continue
                cleaned = effort.strip()
                reasoning_efforts.append({
                    "id": cleaned,
                    "display_name": CODEX_REASONING_LABELS.get(cleaned, cleaned),
                })
        default_reasoning = candidate.get("default_reasoning_level")
        result.append({
            "id": model_id.strip(),
            "display_name": display_name.strip()
                if isinstance(display_name, str) and display_name.strip()
                else model_id.strip(),
            "default_reasoning_effort": default_reasoning.strip()
                if isinstance(default_reasoning, str) and default_reasoning.strip()
                else None,
            "reasoning_efforts": reasoning_efforts,
        })
    return result


def resolve_codex_command() -> tuple[str, ...]:
    codex_path = shutil.which("codex.cmd") or shutil.which("codex")
    if codex_path is None:
        raise BrainUnavailableError("Codex CLI was not found on PATH")

    path = Path(codex_path)
    if path.suffix.lower() == ".cmd":
        node_path = shutil.which("node.exe") or shutil.which("node")
        codex_js = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if node_path is None or not codex_js.is_file():
            raise BrainUnavailableError("Codex npm launcher could not be resolved")
        return (node_path, str(codex_js))

    return (str(path),)


def _extract_response(raw: str) -> BrainResponse:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise BrainResponseError("Codex response did not contain a JSON object")

    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BrainResponseError("Codex response was not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise BrainResponseError("Codex response must be a JSON object")

    say = parsed.get("say")
    actions = parsed.get("actions")
    passed = parsed.get("pass")
    if "to" not in parsed:
        raise BrainResponseError("Codex response did not include required field 'to'")
    addressed_to = parsed.get("to")
    if not isinstance(say, str) or not isinstance(actions, list) or not isinstance(passed, bool):
        raise BrainResponseError("Codex response did not match the talk response shape")
    if addressed_to is not None and not isinstance(addressed_to, str):
        raise BrainResponseError("Codex response field 'to' must be a string or null")
    if any(not isinstance(action, dict) for action in actions):
        raise BrainResponseError("Codex actions must be objects")

    return BrainResponse(
        say=say.strip(),
        actions=tuple(dict(action) for action in actions),
        passed=passed,
        addressed_to=addressed_to.strip() if isinstance(addressed_to, str) and addressed_to.strip() else None,
    )


def _format_history(entries: object) -> str:
    if not isinstance(entries, list):
        return "（履歴なし）"
    lines: list[str] = []
    for entry in entries[-20:]:
        if not isinstance(entry, dict):
            continue
        sender = entry.get("from")
        recipient = entry.get("to")
        text = entry.get("text")
        if not isinstance(sender, str) or not isinstance(text, str):
            continue
        label = "Master" if sender == "master" else sender
        if isinstance(recipient, str):
            label = f"{label} → {'Master' if recipient == 'master' else recipient}"
        lines.append(f"{label}: {text}")
    return "\n".join(lines) or "（履歴なし）"


def _build_talk_prompt(resident: dict[str, Any], context: dict[str, Any]) -> str:
    name = resident.get("name") if isinstance(resident.get("name"), str) else "Resident"
    persona = resident.get("persona") if isinstance(resident.get("persona"), str) else ""
    persona_section = persona.strip() or "固有人格はまだ未設定。自然で簡潔に会話する。"
    history_section = _format_history(context.get("history"))

    return f"""あなたはNiraiという箱庭世界に暮らすResident「{name}」です。
Niraiは水面から光が届く静かな海中3D空間です。Masterは画面のこちら側にいる隣人です。
これは会話モードです。ファイル操作、コマンド実行、Web検索は不要です。会話だけをしてください。
最終応答は指定されたJSON Schemaに従うJSONオブジェクト1個だけにしてください。

人格:
{persona_section}

直近の会話:
{history_section}

Masterの最新発話へ自然に返事をしてください。話すことがなければpass=trueにしてください。
M1-06では行動コマンドはまだ使わないため、actionsは必ず空配列にしてください。
"""


def _build_whisper_prompt(resident: dict[str, Any], context: dict[str, Any]) -> str:
    name = resident.get("name") if isinstance(resident.get("name"), str) else "Resident"
    persona = resident.get("persona") if isinstance(resident.get("persona"), str) else ""
    private_context = context.get("private_context") if isinstance(context.get("private_context"), str) else ""
    recent = _format_history(context.get("recent_whispers"))
    current = _format_history(context.get("current_whisper_history"))
    public = _format_history(context.get("public_history"))
    persona_section = persona.strip() or "固有人格はまだ未設定。自然で簡潔に会話する。"
    private_section = private_context.strip() or "（Private Contextなし）"

    return f"""あなたはNiraiという箱庭世界に暮らすResident「{name}」です。
これはMasterとあなたの1対1のWhisperです。ここで知ったPrivate内容は公開会話へ持ち出してはいけません。
ファイル操作、コマンド実行、Web検索は不要です。Whisperへの返事だけをしてください。
最終応答は指定されたJSON Schemaに従うJSONオブジェクト1個だけにしてください。

人格:
{persona_section}

公開会話の直近Context（秘密は含まれません）:
{public}

Private Context:
{private_section}

直近のWhisper:
{recent}

現在SessionのWhisper:
{current}

Masterの最新Whisperへ自然に返事をしてください。actionsは必ず空配列にしてください。
"""


class CodexDriver:
    def __init__(
        self,
        nirai_root: Path,
        *,
        process_manager: ProcessManager | None = None,
        command_prefix: Sequence[str] | None = None,
    ) -> None:
        self.nirai_root = nirai_root
        self.workspace = nirai_root / "runtime" / "brain_workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.process_manager = process_manager or ProcessManager()
        self.command_prefix = tuple(command_prefix) if command_prefix is not None else resolve_codex_command()
        self.schema_path = Path(__file__).with_name("codex_talk.schema.json")
        self.consult_schema_path = Path(__file__).with_name("codex_consult.schema.json")

    async def think(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> BrainResponse:
        if mode == "talk":
            prompt = build_talk_prompt(resident, context)
            schema_path = self.schema_path
        elif mode == "whisper":
            prompt = build_whisper_prompt(resident, context)
            schema_path = self.schema_path
        elif mode == "consult":
            prompt = build_consult_prompt(resident, context)
            schema_path = self.consult_schema_path
        else:
            raise BrainError(f"CodexDriver does not support mode yet: {mode}")
        model_value = resident.get("brain_model")
        model = model_value.strip() if isinstance(model_value, str) and model_value.strip() else None
        reasoning_value = resident.get("brain_reasoning_effort")
        reasoning_effort = (
            reasoning_value.strip()
            if isinstance(reasoning_value, str) and reasoning_value.strip()
            else None
        )
        for attempt in range(2):
            completed = await self._run_once(
                invocation_id,
                prompt,
                model,
                reasoning_effort,
                schema_path,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or f"exit code {completed.returncode}"
                LOGGER.warning(
                    "codex_cli_failed invocation_id=%s returncode=%s stderr=%s",
                    invocation_id,
                    completed.returncode,
                    detail[:500].replace("\r", "\\r").replace("\n", "\\n"),
                )
                raise BrainError(f"Codex CLI failed: {detail}")
            try:
                if mode == "consult":
                    return extract_consult_result_envelope(completed.stdout, "Codex")
                return _extract_response(completed.stdout)
            except BrainResponseError as exc:
                LOGGER.warning(
                    "codex_parse_failed invocation_id=%s attempt=%s error=%s",
                    invocation_id,
                    attempt + 1,
                    exc,
                )
                if attempt == 1:
                    raise

        raise BrainResponseError("Codex response could not be parsed")

    async def cancel(self, invocation_id: str) -> bool:
        return await self.process_manager.cancel(invocation_id)

    async def _run_once(
        self,
        invocation_id: str,
        prompt: str,
        model: str | None,
        reasoning_effort: str | None,
        schema_path: Path,
    ) -> CompletedInvocation:
        argv = [
            *self.command_prefix,
            *(["--model", model] if model is not None else []),
            *(
                ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
                if reasoning_effort is not None
                else []
            ),
            "--ask-for-approval",
            "never",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "-C",
            str(self.workspace),
            "-",
        ]
        return await self.process_manager.run(
            invocation_id,
            argv,
            cwd=self.workspace,
            timeout_sec=CODEX_TIMEOUT_SEC,
            stdin_text=prompt,
        )
