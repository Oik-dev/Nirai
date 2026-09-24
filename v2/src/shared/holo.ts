import type { HubSettings } from "./settings.js";

export interface ConversationBinding {
  task_id: string;
  provider: string;
  external_conversation_id: string | null;
  external_url: string | null;
}

export interface HoloObservation {
  state: "ready" | "busy" | "blocked" | "unavailable";
  reason: string;
  url: string | null;
  conversation_id: string | null;
  busy: boolean;
  draft: boolean;
}

export interface HoloDispatch {
  turn_id: string;
  task_id: string;
  control_epoch: number;
  target_conversation_id: string | null;
  prompt: string;
  settings: HubSettings;
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
