"""Read Claude Code's local OAuth credential without copying or logging it."""

import json
import os
import time
from pathlib import Path


class ClaudeTokenUnavailable(Exception):
    """Claude Code has no access token in its local credentials file."""


class ClaudeTokenExpired(ClaudeTokenUnavailable):
    """Claude Code's access token is past its expiry time.

    The token only lasts about 8 hours (seen 2026-09-25: expiresAt exactly
    8h after the file was written), and only the claude CLI refreshes it.
    Sending an expired one is worse than useless: other tools report the
    usage endpoint answering it with 429 "rate limited", which sends the
    caller into a long cooldown for the wrong reason. So it is treated like
    no token at all -- which also lets Automatic mode use the cookie."""


class ClaudeTokenReadError(Exception):
    """The local credentials file exists but cannot be used safely."""


def read_claude_code_token() -> tuple[str, str]:
    """Return (access token, subscription type) from Claude Code's config dir.

    CLAUDE_CONFIG_DIR is Claude Code's own override for ~/.claude. Never store
    the returned token in the widget config or include it in an error message.
    """
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    credential_path = config_dir / ".credentials.json"
    try:
        with credential_path.open("r", encoding="utf-8-sig") as credential_file:
            data = json.load(credential_file)
    except FileNotFoundError as exc:
        raise ClaudeTokenUnavailable(
            "Claude Code is not signed in on this machine. Run claude once to sign in, "
            "or choose Cookie and paste a session key in Settings."
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClaudeTokenReadError(
            "Claude Code credentials could not be read. Sign in again with claude "
            "or choose Cookie in Settings."
        ) from exc

    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise ClaudeTokenUnavailable(
            "Claude Code is not signed in on this machine. Run claude once to sign in, "
            "or choose Cookie and paste a session key in Settings."
        )
    expires_at = oauth.get("expiresAt")     # epoch milliseconds
    if (isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool)
            and expires_at / 1000 <= time.time()):
        raise ClaudeTokenExpired(
            "Claude Code's sign-in has expired (it lasts about 8 hours). Run claude once "
            "to refresh it, or choose Cookie and paste a session key in Settings."
        )
    subscription_type = oauth.get("subscriptionType")
    return token.strip(), subscription_type if isinstance(subscription_type, str) else ""
