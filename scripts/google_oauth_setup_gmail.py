"""One-time interactive setup: grant the watcher Gmail send (gmail.send scope).

The SOP 1 watcher emails its working paper (.md + .json) after every
change-triggered run via the Gmail API (phase0_foundations/gmail_send.py). This
uses OAuth — no app password — which works on corporate Google Workspace
accounts where the admin has disabled SMTP app passwords.

Run this once, locally, from a machine with a browser:

    python scripts/google_oauth_setup_gmail.py

It opens a browser for you to sign in and approve, then saves a refreshable token
to GOOGLE_OAUTH_GMAIL_TOKEN_JSON (default: ./google_oauth_gmail_token.json). The
watcher reads that file and refreshes it silently from then on — no browser again
unless you revoke access or delete the token file.

Prerequisites:
- GOOGLE_OAUTH_CLIENT_JSON must point at an OAuth 2.0 client secret (the same
  client used for the Sheets / Drive-inbox features works).
- The Gmail API must be enabled on the Google Cloud project for that client
  (Cloud Console -> APIs & Services -> Library -> Gmail API -> Enable).
- The consent screen must allow the gmail.send scope (a personal account with
  the client in "Testing" mode is fine while the account is a test user).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from phase0_foundations.gmail_send import SCOPE_GMAIL_SEND  # noqa: E402

load_dotenv()


def main() -> None:
    client_json = os.environ.get("GOOGLE_OAUTH_CLIENT_JSON")
    if not client_json:
        raise SystemExit(
            "Set GOOGLE_OAUTH_CLIENT_JSON in .env to your downloaded OAuth "
            "client secret file first (see this script's docstring)."
        )
    if not os.path.exists(client_json):
        raise SystemExit(f"GOOGLE_OAUTH_CLIENT_JSON points at a file that doesn't exist: {client_json}")

    token_path = os.environ.get("GOOGLE_OAUTH_GMAIL_TOKEN_JSON", "google_oauth_gmail_token.json")

    flow = InstalledAppFlow.from_client_secrets_file(client_json, scopes=[SCOPE_GMAIL_SEND])
    creds = flow.run_local_server(port=0)

    Path(token_path).write_text(creds.to_json(), encoding="utf-8")
    print(f"Authorized. Gmail send token saved to {token_path}.")
    print("The watcher will email reports to the configured recipients from now on.")


if __name__ == "__main__":
    main()
