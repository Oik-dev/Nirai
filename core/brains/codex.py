from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any, Sequence

from .base import BrainError, BrainResponse, BrainResponseError, BrainUnavailableError
from .process_manager import CompletedInvocation, ProcessManager


CODEX_TIMEOUT_SEC = 120.0
LOGGER = logging.getLogger("nirai.core.brain.codex")


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
    if not isinstance(say, str) or not isinstance(actions, list) or not isinstance(passed, bool):
        raise BrainResponseError("Codex response did not match the talk response shape")
    if any(not isinstance(action, dict) for action in actions):
        raise BrainResponseError("Codex actions must be objects")

    return BrainResponse(
        say=say.strip(),
        actions=tuple(dict(action) for action in actions),
        passed=passed,
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

    async def think(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> BrainResponse:
        if mode == "talk":
            prompt = _build_talk_prompt(resident, context)
        elif mode == "whisper":
            prompt = _build_whisper_prompt(resident, context)
        else:
            raise BrainError(f"CodexDriver does not support mode yet: {mode}")
        for attempt in range(2):
            completed = await self._run_once(invocation_id, prompt)
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

    async def _run_once(self, invocation_id: str, prompt: str) -> CompletedInvocation:
        argv = [
            *self.command_prefix,
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
            str(self.schema_path),
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
