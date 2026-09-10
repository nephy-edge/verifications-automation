"""Gmail send via the Gmail API (OAuth) — no app password required.

SOP 1 report email delivery for Google Workspace accounts where SMTP app
passwords are disabled by the domain admin (the common corporate case). Uses
the project's existing Google OAuth pattern (see drive_inbox.py): a one-time
browser grant of the `gmail.send` scope, then a silently-refreshed token.

One-time setup:
    python scripts/google_oauth_setup_gmail.py

Sends via the Gmail API with the raw RFC822 message (base64url), so MIME
attachments behave exactly as they would over SMTP.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build

SCOPE_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"

_service_cache: dict[str, object] = {}


class GmailSendError(RuntimeError):
    """Raised when Gmail OAuth is missing/invalid or the API send fails."""


def _token_path() -> str:
    return os.environ.get("GOOGLE_OAUTH_GMAIL_TOKEN_JSON", "google_oauth_gmail_token.json")


def get_gmail_service():
    """Build (and cache) an authenticated Gmail API service with send scope."""
    path = _token_path()
    if path in _service_cache:
        return _service_cache[path]
    if not os.path.exists(path):
        raise GmailSendError(
            f"No Gmail authorization found at '{path}'. Run "
            "`python scripts/google_oauth_setup_gmail.py` once to sign in with "
            "your Google account (grants gmail.send so the watcher can email reports)."
        )
    try:
        creds = UserCredentials.from_authorized_user_file(path, [SCOPE_GMAIL_SEND])
    except (OSError, ValueError) as exc:
        raise GmailSendError(f"Could not read Gmail authorization '{path}': {exc}") from exc
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:  # noqa: BLE001 - surface refresh failures clearly
                raise GmailSendError(
                    f"Gmail token at '{path}' could not be refreshed ({exc}). "
                    "Re-run `python scripts/google_oauth_setup_gmail.py`."
                ) from exc
            Path(path).write_text(creds.to_json(), encoding="utf-8")
        else:
            raise GmailSendError(
                f"Gmail authorization at '{path}' is invalid/revoked. "
                "Re-run `python scripts/google_oauth_setup_gmail.py`."
            )
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    _service_cache[path] = service
    return service


def send_raw_message(raw_mime_bytes: bytes) -> None:
    """Send a raw RFC822 MIME message via the Gmail API as the authorized user."""
    service = get_gmail_service()
    encoded = base64.urlsafe_b64encode(raw_mime_bytes).decode("ascii")
    service.users().messages().send(userId="me", body={"raw": encoded}).execute()
