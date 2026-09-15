"""Shared Google OAuth credential loading for this project's Sheets/Drive/Gmail
integrations (gsheet_export.py, drive_inbox.py, gmail_send.py).

Each feature keeps its own token file, scopes, and error type -- this only
factors out the load/validate/refresh/persist mechanics that were otherwise
duplicated three times.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials


def load_credentials(
    token_path: str,
    scopes: list[str],
    error_cls: type[Exception],
    setup_hint: str,
) -> UserCredentials:
    """Load a user OAuth token from `token_path`, refreshing it if expired.

    Raises `error_cls` (message built around `setup_hint`) if the token file
    is missing, unreadable, invalid/revoked, or fails to refresh. On a
    successful refresh, the renewed token is written back to `token_path`.
    """
    if not os.path.exists(token_path):
        raise error_cls(f"No Google authorization found at '{token_path}'. {setup_hint}")

    try:
        creds = UserCredentials.from_authorized_user_file(token_path, scopes)
    except (OSError, ValueError) as exc:
        raise error_cls(f"Could not read Google authorization at '{token_path}': {exc}") from exc

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:  # noqa: BLE001 - surface refresh failures clearly
                raise error_cls(
                    f"Google authorization at '{token_path}' could not be refreshed ({exc}). {setup_hint}"
                ) from exc
            Path(token_path).write_text(creds.to_json(), encoding="utf-8")
        else:
            raise error_cls(
                f"Google authorization at '{token_path}' is invalid or was revoked. {setup_hint}"
            )

    return creds
