"""Gmail send via the Gmail API (OAuth) — no app password required.

SOP 1 report email delivery for Google Workspace accounts where SMTP app
passwords are disabled by the domain admin (the common corporate case). Uses
the project's existing Google OAuth pattern (see drive_inbox.py): a one-time
browser grant of the `gmail.send` scope, then a silently-refreshed token.

One-time setup:
    python scripts/google_oauth_setup.py --feature gmail

Sends via the Gmail API with the raw RFC822 message (base64url), so MIME
attachments behave exactly as they would over SMTP.
"""

from __future__ import annotations

import base64
import os

from googleapiclient.discovery import build

from phase0_foundations.oauth import load_credentials

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

    creds = load_credentials(
        path,
        [SCOPE_GMAIL_SEND],
        GmailSendError,
        "Run `python scripts/google_oauth_setup.py --feature gmail` to sign in "
        "with your Google account (grants gmail.send so the watcher can email "
        "reports).",
    )
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    _service_cache[path] = service
    return service


def send_raw_message(raw_mime_bytes: bytes) -> None:
    """Send a raw RFC822 MIME message via the Gmail API as the authorized user."""
    service = get_gmail_service()
    encoded = base64.urlsafe_b64encode(raw_mime_bytes).decode("ascii")
    service.users().messages().send(userId="me", body={"raw": encoded}).execute()
