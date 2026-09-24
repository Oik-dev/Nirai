// The Hub persists this definition; adapters use each Turn/Run's frozen copy.
export const DEFAULT_SETTINGS = {
  workspace_scope: null as string | null,
  holo_app_name: null as string | null,
  communication_attempts: 3,
  communication_retry_ms: [1000, 2000],
  delivery_confirmation_ms: 15_000,
  holo_turn_timeout_ms: 25 * 60_000,
  command_timeout_ms: 5 * 60_000,
  command_max_timeout_ms: 30 * 60_000,
  command_output_bytes: 8 * 1024 * 1024,
  control_message_bytes: 1024 * 1024,
  command_acceptance_ms: 5 * 60_000,
};
export type HubSettings = typeof DEFAULT_SETTINGS;
