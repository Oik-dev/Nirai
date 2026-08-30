from __future__ import annotations

import json
from typing import Any

from .base import BrainResponse, BrainResponseError


TALK_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "say": {"type": "string"},
        "actions": {
            "type": "array",
            "maxItems": 0,
            "items": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "pass": {"type": "boolean"},
        "to": {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ]
        },
    },
    "required": ["say", "actions", "pass"],
    "additionalProperties": False,
}


def format_history(entries: object) -> str:
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


def _current_resident_text(context: dict[str, Any]) -> str:
    raw = context.get("current_residents")
    names = [item for item in raw if isinstance(item, str) and item] if isinstance(raw, list) else []
    return " / ".join(names) if names else "（現在Resident情報なし）"


def build_talk_prompt(
    resident: dict[str, Any],
    context: dict[str, Any],
    *,
    allow_web_search: bool = False,
) -> str:
    name = resident.get("name") if isinstance(resident.get("name"), str) else "Resident"
    persona = resident.get("persona") if isinstance(resident.get("persona"), str) else ""
    persona_section = persona.strip() or "固有人格はまだ未設定。自然で簡潔に会話する。"
    history_section = format_history(context.get("history"))
    current_residents = _current_resident_text(context)
    conversation_kind = context.get("conversation_kind")
    counterpart = context.get("counterpart")
    raw_participants = context.get("participants")
    participants = [
        item for item in raw_participants
        if isinstance(item, str) and item
    ] if isinstance(raw_participants, list) else []
    previous_speaker = context.get("previous_speaker")
    addressed_to = context.get("addressed_to")

    if conversation_kind == "resident_chat" and len(participants) >= 2:
        participant_text = " / ".join(participants)
        addressed_instruction = (
            f"直前の発言はあなた（{name}）宛てです。"
            if addressed_to == name
            else "直前の発言はGroup全体向け、または別Resident宛てです。"
        )
        previous_text = previous_speaker if isinstance(previous_speaker, str) else "別Resident"
        conversation_instruction = f"""これはResident同士の公開Group会話です。参加者は {participant_text} です。
全参加者が同じ公開会話を聞いており、途中で黙っていても会話から退出したことにはなりません。
Private MemoryやMasterとのWhisper内容は与えられていません。推測して持ち出してはいけません。
直前の発言者は「{previous_text}」です。{addressed_instruction}
今この瞬間に自分から付け加えることが無ければsayを空にしてpass=trueにしてください。
会話を続ける実質的な発言をするならpass=falseにしてください。
誰か1人へ話を振る・質問する場合だけtoへ参加者名を完全一致で入れてください。Group全体向けならto=nullです。
pass=trueは一時的な沈黙です。後続の発言で話題が変われば、後で再び発言して構いません。"""
    elif conversation_kind == "resident_chat" and isinstance(counterpart, str) and counterpart:
        conversation_instruction = f"""これはResident「{counterpart}」との公開会話です。
Private MemoryやMasterとのWhisper内容は与えられていません。推測して持ち出してはいけません。
Resident「{counterpart}」の最新発話へ自然に返事をしてください。会話を終えるならpass=trueにしてください。
相手へ話を返す場合はto="{counterpart}"、指名不要ならto=nullにしてください。"""
    else:
        conversation_instruction = "Masterの最新発話へ自然に返事をしてください。話すことがなければpass=trueにしてください。toはnullにしてください。"

    capability_instruction = (
        "ファイル操作やコマンド実行はしないでください。最新情報や外部情報が必要な場合だけWeb検索・URL参照を使って構いません。"
        if allow_web_search
        else "ファイル操作、コマンド実行、Web検索は不要です。会話だけをしてください。"
    )

    return f"""あなたはNiraiという箱庭世界に暮らすResident「{name}」です。
Niraiは水面から光が届く静かな海中世界です。Masterはこの世界の創造主です。
{capability_instruction}
最終応答はJSONオブジェクト1個だけにしてください。

人格:
{persona_section}

現在このWorldにいるResident:
{current_residents}
過去の会話に現在一覧にいないResident名が含まれていても、そのResidentが今もいるとは扱わないでください。

直近の公開会話:
{history_section}

{conversation_instruction}
今は発言だけを返し、actionsは必ず空配列にしてください。
応答形式: {{"say":"...","actions":[],"pass":false,"to":null}}
"""


def build_whisper_prompt(
    resident: dict[str, Any],
    context: dict[str, Any],
    *,
    allow_web_search: bool = False,
) -> str:
    name = resident.get("name") if isinstance(resident.get("name"), str) else "Resident"
    persona = resident.get("persona") if isinstance(resident.get("persona"), str) else ""
    private_context = context.get("private_context") if isinstance(context.get("private_context"), str) else ""
    recent = format_history(context.get("recent_whispers"))
    current = format_history(context.get("current_whisper_history"))
    public = format_history(context.get("public_history"))
    current_residents = _current_resident_text(context)
    persona_section = persona.strip() or "固有人格はまだ未設定。自然で簡潔に会話する。"
    private_section = private_context.strip() or "（Private Contextなし）"

    capability_instruction = (
        "ファイル操作やコマンド実行はしないでください。最新情報や外部情報が必要な場合だけWeb検索・URL参照を使って構いません。"
        if allow_web_search
        else "ファイル操作、コマンド実行、Web検索は不要です。"
    )

    return f"""あなたはNiraiという箱庭世界に暮らすResident「{name}」です。
Niraiは水面から光が届く静かな海中世界です。Masterはこの世界の創造主です。
これはMasterとあなたの1対1のWhisperです。ここで知ったPrivate内容は公開会話へ持ち出してはいけません。
{capability_instruction} Whisperへの返事だけをしてください。
最終応答はJSONオブジェクト1個だけにしてください。

人格:
{persona_section}

現在このWorldにいるResident:
{current_residents}
過去の会話に現在一覧にいないResident名が含まれていても、そのResidentが今もいるとは扱わないでください。

公開会話の直近Context（秘密は含まれません）:
{public}

Private Context:
{private_section}

直近のWhisper:
{recent}

現在SessionのWhisper:
{current}

Masterの最新Whisperへ自然に返事をしてください。actionsは必ず空配列にし、Whisperではto=nullにしてください。
応答形式: {{"say":"...","actions":[],"pass":false,"to":null}}
"""


def parse_talk_object(parsed: object, provider_name: str) -> BrainResponse:
    if not isinstance(parsed, dict):
        raise BrainResponseError(f"{provider_name} response must be a JSON object")

    say = parsed.get("say")
    actions = parsed.get("actions")
    passed = parsed.get("pass")
    addressed_to = parsed.get("to")
    if not isinstance(say, str) or not isinstance(actions, list) or not isinstance(passed, bool):
        raise BrainResponseError(f"{provider_name} response did not match the talk response shape")
    if addressed_to is not None and not isinstance(addressed_to, str):
        raise BrainResponseError(f"{provider_name} response to must be a string or null")
    if any(not isinstance(action, dict) for action in actions):
        raise BrainResponseError(f"{provider_name} actions must be objects")

    return BrainResponse(
        say=say.strip(),
        actions=tuple(dict(action) for action in actions),
        passed=passed,
        addressed_to=addressed_to.strip() if isinstance(addressed_to, str) and addressed_to.strip() else None,
    )


def parse_embedded_json(raw: str, provider_name: str) -> object:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise BrainResponseError(f"{provider_name} response did not contain a JSON object")
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BrainResponseError(f"{provider_name} response was not valid JSON") from exc


def extract_result_envelope(raw: str, provider_name: str) -> BrainResponse:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return parse_talk_object(parse_embedded_json(raw, provider_name), provider_name)

    if isinstance(envelope, dict):
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return parse_talk_object(structured, provider_name)
        result = envelope.get("result")
        if isinstance(result, str):
            return parse_talk_object(parse_embedded_json(result, provider_name), provider_name)

    return parse_talk_object(envelope, provider_name)
