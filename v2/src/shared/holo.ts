import type { HubSettings } from "./settings.js";

export interface ConversationBinding {
  task_id: string;
  provider: string;
  external_conversation_id: string | null;
  external_url: string | null;
}

export interface HoloObservation {
  surface_mode: "task" | "chat";
  state: "ready" | "busy" | "blocked" | "unavailable";
  reason: string;
  url: string | null;
  conversation_id: string | null;
  task_id: string | null;
  busy: boolean;
  draft: boolean;
}

export interface HoloDispatch {
  task_id: string;
  turn_id: string;
  target_conversation_id: string | null;
  prompt: string;
  settings: HubSettings;
}

/** A single Holo conversation receives both Say and Whisper without Task authority. */
export interface HoloChatBinding {
  external_conversation_id: string | null;
  external_url: string | null;
}

export interface HoloChatDispatch {
  message_id: string;
  channel: "say" | "whisper";
  audience: string[];
  target_conversation_id: string | null;
  prompt: string;
  settings: HubSettings;
}

export interface HoloChatEnded {
  message_id: string;
  reason: string;
  sent: boolean;
}

// Adapter reports that end a Turn without an assistant Message. `sent` is false only
// when the Adapter can prove the prompt never reached ChatGPT.
export interface HoloEnded {
  turn_id: string;
  reason: string;
  sent: boolean;
}

export function conversationId(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.origin !== "https://chatgpt.com") return null;
    return /^\/(?:g\/[^/]+\/)?c\/([a-zA-Z0-9-]+)\/?$/.exec(parsed.pathname)?.[1] ?? null;
  } catch {
    return null;
  }
}
