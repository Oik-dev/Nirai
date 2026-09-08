from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..agents.base import AgentRunRequest, AgentRuntimeError
from ..agents.codex_app_server import CodexAppServerAdapter
from ..agents.cursor_cli_conversation import CursorCliConversationAdapter
from ..agents.safety import AgentSafetyError, AgentWorkspacePolicy
from .base import BrainDriver, BrainError, BrainResponse, BrainResponseError
from .talk_common import (
    build_consult_prompt,
    build_talk_prompt,
    build_whisper_prompt,
    extract_consult_result_envelope,
    extract_result_envelope,
)


LOGGER = logging.getLogger("nirai.core.brain.native_conversation")
_STATE_VERSION = 3
_NATIVE_PROVIDERS = frozenset({"codex", "cursor", "gemini"})


@dataclass(frozen=True)
class NativeConversationState:
    logical_id: str
    provider: str
    provider_session_id: str
    last_seen_entry_id: str | None
    last_output_entry_id: str | None
    pending_turn: bool
    updated_at: str


class NativeConversationStateStore:
    """Durable mapping from a Nirai logical conversation to provider context.

    Provider session/thread ids are only a transport cache. The logical id and
    Nirai's own chat/memory data remain authoritative and can rebuild context if
    this cache is missing or invalid.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state_root = self.root / "runtime" / "brain_conversations"
        self.state_root.mkdir(parents=True, exist_ok=True)

    def load(self, logical_id: str) -> NativeConversationState | None:
        path = self._path(logical_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") not in {2, _STATE_VERSION}:
                raise ValueError("unsupported state version")
            if raw.get("logical_id") != logical_id:
                raise ValueError("logical id mismatch")
            provider = raw.get("provider")
            provider_session_id = raw.get("provider_session_id")
            last_seen_entry_id = raw.get("last_seen_entry_id")
            last_output_entry_id = raw.get("last_output_entry_id")
            pending_turn = raw.get("pending_turn")
            updated_at = raw.get("updated_at")
            if provider not in _NATIVE_PROVIDERS:
                raise ValueError("unsupported provider")
            if not isinstance(provider_session_id, str) or not provider_session_id:
                raise ValueError("provider session id is missing")
            if last_seen_entry_id is not None and not isinstance(last_seen_entry_id, str):
                raise ValueError("last seen entry id is invalid")
            if last_output_entry_id is not None and not isinstance(last_output_entry_id, str):
                raise ValueError("last output entry id is invalid")
            if not isinstance(pending_turn, bool):
                raise ValueError("pending turn flag is invalid")
            if not isinstance(updated_at, str) or not updated_at:
                raise ValueError("updated_at is invalid")
            return NativeConversationState(
                logical_id=logical_id,
                provider=provider,
                provider_session_id=provider_session_id,
                last_seen_entry_id=last_seen_entry_id,
                last_output_entry_id=last_output_entry_id,
                pending_turn=pending_turn,
                updated_at=updated_at,
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # Native provider state is derived transport context. Corruption must
            # never poison Nirai identity/memory; rebuild from Nirai data.
            LOGGER.warning(
                "native_brain_state_invalid logical_id=%s reset=true",
                logical_id,
                exc_info=True,
            )
            try:
                path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("native_brain_state_invalid_cleanup_failed", exc_info=True)
            return None

    def save(
        self,
        logical_id: str,
        provider: str,
        provider_session_id: str,
        *,
        last_seen_entry_id: str | None,
        last_output_entry_id: str | None = None,
        pending_turn: bool = False,
    ) -> NativeConversationState:
        if provider not in _NATIVE_PROVIDERS:
            raise BrainError(f"Native Conversation provider is unsupported: {provider}")
        cleaned_session = provider_session_id.strip()
        if not cleaned_session:
            raise BrainError("Native Conversation provider session id is empty")
        state = NativeConversationState(
            logical_id=logical_id,
            provider=provider,
            provider_session_id=cleaned_session,
            last_seen_entry_id=last_seen_entry_id,
            last_output_entry_id=last_output_entry_id,
            pending_turn=bool(pending_turn),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        path = self._path(logical_id)
        payload = {
            "version": _STATE_VERSION,
            "logical_id": state.logical_id,
            "provider": state.provider,
            "provider_session_id": state.provider_session_id,
            "last_seen_entry_id": state.last_seen_entry_id,
            "last_output_entry_id": state.last_output_entry_id,
            "pending_turn": state.pending_turn,
            "updated_at": state.updated_at,
        }
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise BrainError("Native Conversation state could not be persisted") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning(
                    "native_brain_state_temp_cleanup_failed logical_id=%s",
                    logical_id,
                    exc_info=True,
                )
        return state

    def delete(self, logical_id: str) -> None:
        try:
            self._path(logical_id).unlink(missing_ok=True)
        except OSError as exc:
            raise BrainError("Native Conversation state could not be deleted") from exc

    def iter_states(self) -> Iterator[NativeConversationState]:
        for path in self.state_root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            logical_id = raw.get("logical_id")
            if not isinstance(logical_id, str) or not logical_id:
                continue
            state = self.load(logical_id)
            if state is not None:
                yield state

    def delete_prefix(self, logical_id_prefix: str) -> list[NativeConversationState]:
        removed: list[NativeConversationState] = []
        for state in tuple(self.iter_states()):
            if not state.logical_id.startswith(logical_id_prefix):
                continue
            removed.append(state)
            try:
                self._path(state.logical_id).unlink(missing_ok=True)
            except OSError:
                LOGGER.warning(
                    "native_brain_state_prefix_cleanup_failed logical_id=%s",
                    state.logical_id,
                    exc_info=True,
                )
        return removed

    def _path(self, logical_id: str) -> Path:
        digest = hashlib.sha256(logical_id.encode("utf-8")).hexdigest()
        return self.state_root / f"{digest}.json"


class NativeConversationBrainService:
    """Provider-native multi-turn transport for ordinary Resident Brain calls."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state = NativeConversationStateStore(self.root)
        self.workspace_policy = AgentWorkspacePolicy(self.root, ("runtime\\workspace",))
        # Optional provider transports are deliberately lazy. A missing or broken
        # provider CLI must never prevent Nirai Core/World from starting; the
        # dependency is resolved only when that provider is actually used.
        self._adapters: dict[str, CodexAppServerAdapter | CursorCliConversationAdapter] = {}
        self._adapter_factories: dict[
            str,
            Callable[[], CodexAppServerAdapter | CursorCliConversationAdapter],
        ] = {
            "codex": lambda: CodexAppServerAdapter(self.workspace_policy),
            "cursor": lambda: CursorCliConversationAdapter(self.root),
        }
        # Provider-native sessions retain already-delivered Conversation context.
        # These caches suppress needless re-injection while the provider working
        # context is healthy. They are intentionally process-local: after a Core
        # restart Nirai refreshes once even if the provider Session/Thread resumes.
        self._static_context_hashes: dict[str, str] = {}
        self._injected_memory_hashes: dict[str, set[str]] = {}
        self._conversation_locks: dict[str, asyncio.Lock] = {}

    def supports(self, provider: str | None) -> bool:
        return isinstance(provider, str) and provider in _NATIVE_PROVIDERS

    def last_seen_entry_id(self, logical_id: str, provider: str) -> str | None:
        state = self.state.load(logical_id)
        if state is None or state.provider != provider or state.pending_turn:
            return None
        return state.last_seen_entry_id

    def last_output_entry_id(self, logical_id: str, provider: str) -> str | None:
        state = self.state.load(logical_id)
        if state is None or state.provider != provider or state.pending_turn:
            return None
        return state.last_output_entry_id

    def has_compatible_state(self, logical_id: str, provider: str) -> bool:
        state = self.state.load(logical_id)
        return (
            state is not None
            and state.provider == provider
            and not state.pending_turn
        )

    def mark_seen(
        self,
        logical_id: str,
        provider: str,
        entry_id: str | None,
        *,
        output_entry_id: str | None = None,
    ) -> None:
        state = self.state.load(logical_id)
        if state is None or state.provider != provider:
            return
        try:
            self.state.save(
                logical_id,
                provider,
                state.provider_session_id,
                last_seen_entry_id=entry_id,
                last_output_entry_id=output_entry_id,
                pending_turn=False,
            )
        except BrainError:
            # The Nirai transcript is already the durable commit point. If its
            # transport marker cannot be persisted, invalidate native context
            # instead of turning a successfully committed reply into a failed
            # user turn or risking replay from a stale Provider position.
            try:
                self.reset(logical_id)
            except BrainError:
                LOGGER.warning(
                    "native_brain_reset_after_seen_persist_failure_failed logical_id=%s provider=%s",
                    logical_id,
                    provider,
                    exc_info=True,
                )
            LOGGER.warning(
                "native_brain_seen_persist_failed logical_id=%s provider=%s rebuilt_next_turn=true",
                logical_id,
                provider,
                exc_info=True,
            )

    def persist_pending_turn(
        self,
        logical_id: str,
        provider: str,
        provider_session_id: str,
        *,
        last_seen_entry_id: str | None,
    ) -> None:
        """Persist a Provider-advanced turn or invalidate that transport cache.

        Once a Provider has consumed a turn, failure to durably record the new
        native session position makes the transport context ambiguous. Never
        leave the previous mapping resumable in that case: discard it and force
        the next Nirai turn to rebuild from the authoritative local transcript.
        """
        previous = self.state.load(logical_id)
        previous_output_entry_id = (
            previous.last_output_entry_id
            if previous is not None and previous.provider == provider
            else None
        )
        try:
            self.state.save(
                logical_id,
                provider,
                provider_session_id,
                last_seen_entry_id=last_seen_entry_id,
                last_output_entry_id=previous_output_entry_id,
                pending_turn=True,
            )
        except BrainError:
            try:
                self.reset(logical_id)
            except BrainError:
                LOGGER.warning(
                    "native_brain_reset_after_state_persist_failure_failed logical_id=%s provider=%s",
                    logical_id,
                    provider,
                    exc_info=True,
                )
            raise

    def reset(self, logical_id: str) -> None:
        self._static_context_hashes.pop(logical_id, None)
        self._injected_memory_hashes.pop(logical_id, None)
        state = self.state.load(logical_id)
        if state is not None:
            self._discard_provider_context(state.provider, logical_id)
        else:
            # A provider turn may have created native state before Nirai could
            # durably persist its session id (for example an invalid JSON turn).
            # Clear any provider-local conversation cache keyed by logical_id
            # even when the transport mapping itself is absent.
            for provider in tuple(self._adapters):
                self._discard_provider_context(provider, logical_id)
        self.state.delete(logical_id)
        self._cleanup_logical_workspace(logical_id)

    def reset_prefix(self, logical_id_prefix: str) -> None:
        states = self.state.delete_prefix(logical_id_prefix)
        for state in states:
            self._static_context_hashes.pop(state.logical_id, None)
            self._injected_memory_hashes.pop(state.logical_id, None)
            self._discard_provider_context(state.provider, state.logical_id)
            self._cleanup_logical_workspace(state.logical_id)

    def reset_resident(self, resident_name: str) -> None:
        suffixes = (
            f":public:{resident_name}",
            f":whisper:{resident_name}",
            f":resident:{resident_name}",
        )
        logical_ids = [
            state.logical_id
            for state in self.state.iter_states()
            if state.logical_id.endswith(suffixes)
        ]
        for logical_id in logical_ids:
            self.reset(logical_id)

    def conversation_lock(self, logical_id: str) -> asyncio.Lock:
        lock = self._conversation_locks.get(logical_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[logical_id] = lock
        return lock

    async def think(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
        *,
        provider: str,
        logical_id: str,
    ) -> BrainResponse:
        # Core acquires this same logical lock before selecting the unseen Nirai
        # history delta, then keeps it through local response commit. Direct
        # callers that do not own that wider transaction retain the service's
        # original self-locking behavior.
        if context.get("_native_lock_held") is True:
            return await self._think_locked(
                invocation_id,
                mode,
                resident,
                context,
                provider=provider,
                logical_id=logical_id,
            )
        async with self.conversation_lock(logical_id):
            return await self._think_locked(
                invocation_id,
                mode,
                resident,
                context,
                provider=provider,
                logical_id=logical_id,
            )

    async def _think_locked(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
        *,
        provider: str,
        logical_id: str,
    ) -> BrainResponse:
        adapter = self._get_adapter(provider)

        current = self.state.load(logical_id)
        if current is not None and (current.provider != provider or current.pending_turn):
            # A pending state means the Provider already consumed a turn but
            # Nirai never committed the corresponding local response marker.
            # The Provider cache is therefore ahead of the authoritative
            # transcript and must be discarded before rebuilding from Nirai.
            self.reset(logical_id)
            current = None
        provider_session_id = current.provider_session_id if current is not None else None
        last_seen_entry_id = current.last_seen_entry_id if current is not None else None
        prompt_context, static_context_hash, delivered_memory_hashes = self.prepare_prompt_context(
            logical_id,
            mode,
            resident,
            context,
        )
        prompt = self._build_prompt(mode, resident, prompt_context)
        context_compacted = False

        try:
            raw, provider_session_id, turn_compacted = await self._run_turn(
                invocation_id,
                adapter=adapter,
                provider=provider,
                logical_id=logical_id,
                resident=resident,
                prompt=prompt,
                provider_session_id=provider_session_id,
            )
            context_compacted = context_compacted or turn_compacted
            try:
                response = self._parse_response(mode, raw, provider)
            except BrainResponseError:
                # Native context already contains the failed answer and original
                # prompt, so ask only for a format repair instead of replaying
                # the user's turn a second time.
                repair_prompt = (
                    "Your previous response did not match Nirai's required JSON shape. "
                    "Re-answer the immediately preceding Nirai turn as exactly one JSON object, "
                    "with no prose before or after it. Preserve the intended content."
                )
                raw, provider_session_id, repair_compacted = await self._run_turn(
                    f"{invocation_id}-repair",
                    adapter=adapter,
                    provider=provider,
                    logical_id=logical_id,
                    resident=resident,
                    prompt=repair_prompt,
                    provider_session_id=provider_session_id,
                )
                context_compacted = context_compacted or repair_compacted
                response = self._parse_response(mode, raw, provider)
        except (AgentRuntimeError, AgentSafetyError, BrainResponseError, OSError) as exc:
            # A failed provider turn has ambiguous native state: it may have
            # consumed part of the prompt before failing. Forget the transport
            # id so the next Nirai turn rebuilds from authoritative local data.
            try:
                self.reset(logical_id)
            except BrainError:
                LOGGER.warning(
                    "native_brain_reset_after_failure_failed logical_id=%s provider=%s",
                    logical_id,
                    provider,
                    exc_info=True,
                )
            if isinstance(exc, BrainResponseError):
                raise
            raise BrainError(str(exc) or type(exc).__name__) from exc

        self.persist_pending_turn(
            logical_id,
            provider,
            provider_session_id,
            last_seen_entry_id=last_seen_entry_id,
        )
        if context_compacted:
            # Keep the same provider Conversation identity. Only invalidate
            # assumptions about what survived its internal compaction; the next
            # turn will refresh static context and may re-inject relevant memory.
            self._static_context_hashes.pop(logical_id, None)
            self._injected_memory_hashes.pop(logical_id, None)
        else:
            self.mark_context_delivered(
                logical_id,
                static_context_hash,
                delivered_memory_hashes,
            )
        return response

    async def cancel(self, invocation_id: str) -> bool:
        cancelled = False
        for adapter in self._adapters.values():
            try:
                cancelled = (await adapter.cancel(invocation_id)) or cancelled
                cancelled = (await adapter.cancel(f"{invocation_id}-repair")) or cancelled
            except Exception:
                LOGGER.warning(
                    "native_brain_cancel_failed invocation_id=%s provider=%s",
                    invocation_id,
                    adapter.provider,
                    exc_info=True,
                )
        return cancelled

    def prepare_prompt_context(
        self,
        logical_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], str, set[str]]:
        """Prepare only context the provider has not already received.

        Native Session/Thread state is a working-context cache, not long-term
        memory. Repeated World/Private Memory evidence is suppressed only while
        that working context is known healthy; Core restart, provider reset, or
        a detected compaction makes the evidence eligible for injection again.
        """
        static_context_hash = self._static_context_hash(mode, resident, context)
        prompt_context = dict(context)
        prompt_context["_native_static_refresh"] = (
            self._static_context_hashes.get(logical_id) != static_context_hash
        )

        delivered_memory_hashes: set[str] = set()
        already_delivered = self._injected_memory_hashes.get(logical_id, set())
        for context_key in ("world_memories", "private_memories"):
            raw_memories = context.get(context_key)
            if not isinstance(raw_memories, list):
                continue
            unseen: list[object] = []
            for memory in raw_memories:
                if not isinstance(memory, dict):
                    continue
                memory_hash = self._memory_context_hash(memory)
                if memory_hash in already_delivered:
                    continue
                unseen.append(memory)
                delivered_memory_hashes.add(memory_hash)
            prompt_context[context_key] = unseen
        return prompt_context, static_context_hash, delivered_memory_hashes

    def mark_context_delivered(
        self,
        logical_id: str,
        static_context_hash: str,
        memory_hashes: set[str],
    ) -> None:
        self._static_context_hashes[logical_id] = static_context_hash
        if memory_hashes:
            self._injected_memory_hashes.setdefault(logical_id, set()).update(memory_hashes)

    @staticmethod
    def _memory_context_hash(memory: dict[str, Any]) -> str:
        encoded = json.dumps(
            memory,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _run_turn(
        self,
        invocation_id: str,
        *,
        adapter: CodexAppServerAdapter | CursorCliConversationAdapter,
        provider: str,
        logical_id: str,
        resident: dict[str, Any],
        prompt: str,
        provider_session_id: str | None,
    ) -> tuple[str, str, bool]:
        task_id = self._task_id(logical_id)
        working_dir = self.workspace_policy.resolve_working_dir(None, task_id=task_id)
        captured_session_id = provider_session_id
        context_compacted = False

        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            nonlocal captured_session_id, context_compacted
            if event_type == "run_state":
                value = payload.get("provider_session_id")
                if isinstance(value, str) and value.strip():
                    captured_session_id = value.strip()
                if payload.get("context_compacted") is True:
                    context_compacted = True

        async def no_master(*_args: object, **_kwargs: object) -> dict[str, Any]:
            raise BrainError("Read-only Resident conversation unexpectedly requested Master approval")

        request = AgentRunRequest(
            task_id=task_id,
            agent_session_id=invocation_id,
            resident=str(resident.get("name") or "Resident"),
            provider=provider,
            prompt=prompt,
            working_dir=working_dir,
            model=resident.get("brain_model") if isinstance(resident.get("brain_model"), str) else None,
            reasoning_effort=(
                resident.get("brain_reasoning_effort")
                if isinstance(resident.get("brain_reasoning_effort"), str)
                else None
            ),
            read_only=True,
            purpose="resident_brain",
            conversation_id=logical_id,
            provider_session_id=provider_session_id,
        )
        raw = await adapter.run(request, emit=emit, wait_for_master=no_master)
        if not isinstance(raw, str) or not raw.strip():
            raise BrainResponseError(f"{provider} returned no Resident conversation response")
        if not isinstance(captured_session_id, str) or not captured_session_id.strip():
            raise BrainError(f"{provider} completed without a resumable native conversation id")
        return raw.strip(), captured_session_id.strip(), context_compacted

    @staticmethod
    def _static_context_hash(
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        payload = {
            "mode": mode,
            "name": resident.get("name"),
            "persona": resident.get("persona"),
            "skills": context.get("skills"),
            "conversation_kind": context.get("conversation_kind"),
            "counterpart": context.get("counterpart"),
            "participants": context.get("participants"),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _build_prompt(mode: str, resident: dict[str, Any], context: dict[str, Any]) -> str:
        if mode == "talk":
            return build_talk_prompt(resident, context)
        if mode == "whisper":
            return build_whisper_prompt(resident, context)
        if mode == "consult":
            return build_consult_prompt(resident, context)
        raise BrainError(f"Unsupported native Resident Brain mode: {mode}")

    @staticmethod
    def _parse_response(mode: str, raw: str, provider: str) -> BrainResponse:
        if mode == "consult":
            return extract_consult_result_envelope(raw, provider)
        return extract_result_envelope(raw, provider)

    def _get_adapter(
        self,
        provider: str,
    ) -> CodexAppServerAdapter | CursorCliConversationAdapter:
        adapter = self._adapters.get(provider)
        if adapter is not None:
            return adapter
        factory = self._adapter_factories.get(provider)
        if factory is None:
            raise BrainError(f"Native Conversation provider is unsupported: {provider}")
        try:
            adapter = factory()
        except (AgentRuntimeError, AgentSafetyError, OSError) as exc:
            raise BrainError(str(exc) or type(exc).__name__) from exc
        self._adapters[provider] = adapter
        return adapter

    def _discard_provider_context(self, provider: str, logical_id: str) -> None:
        # Reset must not instantiate an unavailable optional provider merely to
        # discard context. Only transports already used in this Core process can
        # own process-local context that needs a provider-specific cleanup call.
        adapter = self._adapters.get(provider)
        if adapter is None:
            return
        discard = getattr(adapter, "discard_conversation_context", None)
        if callable(discard):
            try:
                discard(logical_id)
            except Exception:
                LOGGER.warning(
                    "native_brain_provider_context_discard_failed logical_id=%s provider=%s",
                    logical_id,
                    provider,
                    exc_info=True,
                )

    def _cleanup_logical_workspace(self, logical_id: str) -> None:
        path = self.workspace_policy.default_workspace_root / self._task_id(logical_id)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except OSError:
            LOGGER.warning(
                "native_brain_workspace_cleanup_failed logical_id=%s",
                logical_id,
                exc_info=True,
            )

    @staticmethod
    def _task_id(logical_id: str) -> str:
        digest = hashlib.sha256(logical_id.encode("utf-8")).hexdigest()[:24]
        return f"BC-{digest}"


class NativeConversationBrainDriver:
    """BrainDriver facade: native continuation when a logical id is supplied."""

    def __init__(
        self,
        provider: str,
        service: NativeConversationBrainService,
        fallback: BrainDriver | Callable[[], BrainDriver],
    ) -> None:
        self.provider = provider
        self.service = service
        if callable(fallback):
            self._fallback: BrainDriver | None = None
            self._fallback_factory: Callable[[], BrainDriver] | None = fallback
        else:
            self._fallback = fallback
            self._fallback_factory = None

    @property
    def fallback(self) -> BrainDriver:
        if self._fallback is not None:
            return self._fallback
        factory = self._fallback_factory
        if factory is None:
            raise BrainError(f"Fallback Brain provider is unavailable: {self.provider}")
        self._fallback = factory()
        return self._fallback

    async def think(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> BrainResponse:
        native = context.get("_native_conversation")
        logical_id = native.get("logical_id") if isinstance(native, dict) else None
        if not isinstance(logical_id, str) or not logical_id.strip():
            return await self.fallback.think(invocation_id, mode, resident, context)
        return await self.service.think(
            invocation_id,
            mode,
            resident,
            context,
            provider=self.provider,
            logical_id=logical_id,
        )

    async def cancel(self, invocation_id: str) -> bool:
        native_cancelled = await self.service.cancel(invocation_id)
        # Cancelling a native-only turn must not initialize an otherwise unused
        # optional fallback CLI merely for cleanup.
        fallback_cancelled = False
        if self._fallback is not None:
            fallback_cancelled = await self._fallback.cancel(invocation_id)
        return native_cancelled or fallback_cancelled
