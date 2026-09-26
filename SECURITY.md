# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: open the
repository's **Security** tab and choose **Report a vulnerability**. Only the
latest release is supported.

Never include your Claude `sessionKey`, the contents of
`~/.claude/.credentials.json`, or any other token in a report, issue or
screenshot. Nothing in this project needs them to diagnose a problem.

## What TwinRails handles

TwinRails reads login credentials so that it can fetch your usage, so here is
exactly what it touches. See also [Risks and limits](README.md#risks-and-limits).

**Credentials**

- **Claude Code's OAuth token.** Read from `.credentials.json` in Claude
  Code's config folder (`%USERPROFILE%\.claude`, or `CLAUDE_CONFIG_DIR` when
  set) each time it is needed. It is never copied into TwinRails' own config
  and never written to a log or error message.
- **Claude.ai `sessionKey` cookie**, only if you paste one in Settings.
  Encrypted with Windows DPAPI (`CryptProtectData`) before it is saved, so
  only your Windows account on that PC can decrypt it.
- **Antigravity's local CSRF token.** Read from the running Antigravity
  language server's command line and only sent back to that server.

**Network**

- `api.anthropic.com` and `claude.ai`, for Claude usage.
- `127.0.0.1`, for the Antigravity language server on your own PC.

There is no analytics, telemetry or update check.

**Files it writes**

- `%USERPROFILE%\.ai_session_limits\config.json`: settings, plus the
  encrypted `sessionKey` if you entered one.
- `%USERPROFILE%\.ai_session_limits\usage_response_debug.log`: written only
  when Claude's usage response contains fields this version doesn't
  recognise. It records key names and value types, never values, so it is
  safe to attach to a bug report.
- A `TwinRails.lnk` shortcut in your Startup folder, only while **Start
  automatically with Windows** is ticked.
