"""One-time interactive setup for the SOP 1 statement drop-folder (Drive inbox).

Authorizes the app to *read* a Google Drive folder where bank/wallet statement
files are dropped, so `cash_watcher.py` can fetch them as the independent side
of reconciliation. Uses a dedicated read-only scope (`drive.readonly`) and its
own token, separate from the "Download as Google Sheet" OAuth token
(gsheet_export.py), so the two features never share or clobber each other's grant.

Run this once, locally, from a machine with a browser:

    python scripts/google_oauth_setup_inbox.py

It opens a browser for you to sign in and approve, then saves a refreshable token
to GOOGLE_OAUTH_INBOX_TOKEN_JSON (default: ./google_oauth_inbox_token.json). The
watcher reads that file and refreshes it silently from then on — no browser again
unless you revoke access or delete the token file.

Requires GOOGLE_OAUTH_CLIENT_JSON to point at an OAuth 2.0 client secret
downloaded from Google Cloud Console (same client works as for the Sheets
feature).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from phase0_foundations.drive_inbox import SCOPE_READONLY  # noqa: E402

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

    token_path = os.environ.get("GOOGLE_OAUTH_INBOX_TOKEN_JSON", "google_oauth_inbox_token.json")

    flow = InstalledAppFlow.from_client_secrets_file(client_json, scopes=[SCOPE_READONLY])
    creds = flow.run_local_server(port=0)

    Path(token_path).write_text(creds.to_json(), encoding="utf-8")
    print(f"Authorized. Drive-inbox read-only token saved to {token_path}.")
    print("The watcher will use this automatically from now on.")


if __name__ == "__main__":
    main()
