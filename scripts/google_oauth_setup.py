"""One-time interactive setup for the "Download as Google Sheet" feature
(phase0_foundations/gsheet_export.py).

Authorizes the app to create Google Sheets under *your own* Google account
instead of the app's service account (which has zero Drive storage quota of
its own — see gsheet_export.py's module docstring for why that fails).

Run this once, locally, from a machine with a browser:

    python scripts/google_oauth_setup.py

It opens a browser for you to sign in and approve, then saves a refreshable
token to GOOGLE_OAUTH_TOKEN_JSON (default: ./google_oauth_token.json). The
running app reads that file and refreshes it silently from then on — no
browser needed again unless you revoke access or delete the token file.

Requires GOOGLE_OAUTH_CLIENT_JSON to point at an OAuth 2.0 client secret
downloaded from Google Cloud Console:
    APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
    -> Application type: Desktop app -> Download JSON
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from phase0_foundations.gsheet_export import SCOPES  # noqa: E402

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

    token_path = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "google_oauth_token.json")

    flow = InstalledAppFlow.from_client_secrets_file(client_json, scopes=SCOPES)
    creds = flow.run_local_server(port=0)

    Path(token_path).write_text(creds.to_json(), encoding="utf-8")
    print(f"Authorized. Token saved to {token_path}.")
    print("The running app will use this automatically from now on.")


if __name__ == "__main__":
    main()
