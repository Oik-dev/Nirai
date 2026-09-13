"""Existing Cursor limits and isolation exclusions, shared by its runtime components."""

ACP_REQUEST_TIMEOUT_SEC = 30.0
ACP_STOP_STEP_TIMEOUT_SEC = 3.0
CURSOR_HOME_CLEANUP_RETRIES = 3
CURSOR_STAGE_CLEANUP_RETRIES = 3
CURSOR_STALE_RUNTIME_AGE_SEC = 6 * 60 * 60
CURSOR_STAGE_FILE_LIMIT = 20_000
CURSOR_STAGE_BYTE_LIMIT = 1_000_000_000
CURSOR_DIFF_TEXT_FILE_LIMIT = 1_000_000
CURSOR_EXACT_CLI_TIMEOUT_SEC = 60.0 * 60.0
CURSOR_EXTERNAL_TOOL_KINDS = {
    "search",
    "fetch",
    "web",
    "web_search",
    "websearch",
    "web_fetch",
    "webfetch",
    "browser",
    "browser_use",
    "computer",
    "computer_use",
    "mcp",
}
CURSOR_WRITABLE_IGNORE_NAMES = frozenset({
    ".cursor",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    ".next",
    "coverage",
    "dist",
    "build",
    "out",
    "Binaries",
    "DerivedDataCache",
    "Intermediate",
    ".vs",
})
CURSOR_READ_ONLY_IGNORE_NAMES = CURSOR_WRITABLE_IGNORE_NAMES
CURSOR_NIRAI_REVIEW_IGNORE_NAMES = frozenset({
    "./.tools",
    "./.vrm",
    "./.vrma",
    "./runtime",
    "./avatars",
    "./material",
    "./world_memory",
    ".env",
    ".env.*",
})
CURSOR_NIRAI_INTEGRATED_AUDIT_IGNORE_NAMES = frozenset({
    "./.vrm",
    "./.vrma",
    "./runtime",
    "./avatars",
    "./material",
    "./world_memory",
    ".env",
    ".env.*",
})

_ALLOWED_ENV_NAMES = {
    "APPDATA",
    "COMSPEC",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
}
