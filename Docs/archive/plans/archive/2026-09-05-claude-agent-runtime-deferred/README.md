# M4 Claude Agent Runtime deferred work (2026-09-05)

## Status

This directory preserves the unfinished Claude Agent Runtime slice that was explored after the M4 Cursor ACP baseline became SAFE.

Master decided to defer Claude Agent Runtime because the current Claude Code authentication path requires a Claude Pro/Max subscription or API-key billing, and Master does not want to add that paid dependency now.

This is an archive, not current Nirai product code. Do not wire these files back into Core unless Master explicitly reopens Claude Agent Runtime work.

## What was confirmed

- PyPI `claude-agent-sdk==0.2.152` was installed temporarily for investigation, then uninstalled when the slice was deferred.
- The wheel contains a native bundled Claude Code executable.
- Bundled CLI version observed: `2.1.259 (Claude Code)`.
- SDK types inspected locally: `ClaudeSDKClient`, `ClaudeAgentOptions`, `can_use_tool`, permission results/updates, structured messages, interrupt/disconnect.
- Claude Code current CLI exposes `--restricted`, `--safe-mode`, `--strict-mcp-config`, `--permission-prompts host`, model and effort controls.
- `claude auth status` returned `loggedIn: false`.
- Browser authentication then showed that Claude Code connection requires Max/Pro subscription or API key. Master declined to subscribe, so no live Claude Agent Runtime smoke was performed.

## Prototype design

`claude_agent.py` contains the prototype `ClaudeAgentSdkAdapter`.

The prototype kept Brain Driver and Agent Runtime separate and attempted to:

- use SDK `can_use_tool` as the Nirai Master approval bridge;
- isolate user/project/local settings, skills, plugins and MCP;
- use restricted/safe-mode execution;
- deny Web/MCP/SubAgent in the baseline slice;
- allow task-workspace reads while Master-gating Edit/Write/Bash;
- bridge `AskUserQuestion` into the common Nirai Question UI;
- suppress private Thinking blocks;
- bound tool output/diff diagnostics;
- remove unrelated parent-process secrets from the Claude child environment;
- reject silent `xhigh` reasoning downgrade;
- restrict `approve_session` to same-tool `addRules + allow` suggestions;
- ensure SDK disconnect runs even when connect fails after spawning;
- rely on SDK 0.2.152's own bounded terminate/kill cleanup rather than cancelling that cleanup with an outer `asyncio.wait_for`.

The accompanying `test_claude_agent_runtime.py` was at 14 focused tests during the prototype cycle. The broader targeted set (Claude/Cursor/Manager/Agent protocol) reached 80 passed before the authentication blocker was accepted as a product constraint.

## Current-product changes that were intentionally removed

The prototype temporarily changed these active files, but those changes were removed when this work was deferred so the active tree returns to the reviewed Cursor SAFE baseline:

- `requirements.txt`: temporary `claude-agent-sdk==0.2.152` dependency
- `core/agents/__init__.py`: Claude adapter export
- `core/agents/manager.py`: default Claude adapter registration and temporary provider-capability helper
- `core/server.py`: Claude availability and capability wiring
- `core/brains/claude_code.py`: fallback to the SDK-bundled Claude executable
- `core/tests/test_claude_driver.py`: SDK-bundled CLI fallback regression
- `core/tests/test_cursor_agent_runtime.py`: assertion that Claude Agent Runtime is registered

## Reopen conditions

Reopen only if one of these becomes acceptable/available:

1. Master obtains Claude Pro/Max and wants Nirai to use that subscription for Claude Code, or
2. Master explicitly accepts API-key billing for this Agent Runtime, or
3. Anthropic provides another supported local authentication path that fits Nirai's cost policy.

On reopen, re-check the then-current official Agent SDK and Claude Code permission semantics before restoring the prototype. Do not assume SDK 0.2.152 / CLI 2.1.259 behavior is still current.

## Archived probes

`probes/` contains the temporary local inspection/auth/cleanup scripts used during this investigation. They are reference material only and are not part of the product execution path.

The SDK uninstall succeeded, but pip reported that a temporary `~laude_agent_sdk` directory under the Python site-packages directory could not be removed automatically. That path is outside Nirai's Local MCP allowed root and was intentionally not touched by the project cleanup.
