"""
Read OpenAI Codex CLI local OAuth credentials without copying or logging them.
"""

import json
import os
from pathlib import Path
from typing import Tuple


class ChatGPTTokenUnavailable(Exception):
    """Codex CLI has no access token in its local credentials file."""


class ChatGPTTokenExpired(ChatGPTTokenUnavailable):
    """Codex CLI access token is past its expiry time."""


class ChatGPTTokenReadError(Exception):
    """The local Codex credentials file exists but cannot be used safely."""


def get_codex_auth_path() -> Path:
    config_dir = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return config_dir / "auth.json"


def read_codex_token() -> Tuple[str, str]:
    """Return (access token, account_id) from Codex CLI's config dir.

    CODEX_HOME is Codex's override for ~/.codex. Never store the returned
    token in the widget config or include it in an error message.
    """
    credential_path = get_codex_auth_path()
    try:
        with credential_path.open("r", encoding="utf-8-sig") as credential_file:
            data = json.load(credential_file)
    except FileNotFoundError as exc:
        raise ChatGPTTokenUnavailable(
            "Codex CLI is not signed in on this machine. Run 'codex' to sign in, "
            "or enter an access token in Settings."
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChatGPTTokenReadError(
            "Codex credentials could not be read. Sign in again with 'codex' "
            "or enter an access token in Settings."
        ) from exc

    if not isinstance(data, dict):
        raise ChatGPTTokenReadError("Malformed Codex credentials file.")

    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        tokens = data  # fallback if tokens are flat in root

    token = tokens.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise ChatGPTTokenUnavailable(
            "Codex CLI is not signed in on this machine. Run 'codex' to sign in, "
            "or enter an access token in Settings."
        )

    account_id = tokens.get("account_id") or ""
    if not isinstance(account_id, str):
        account_id = ""

    return token.strip(), account_id.strip()
