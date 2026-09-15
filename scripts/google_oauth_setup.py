"""One-time interactive setup for this project's Google OAuth integrations:

    --feature sheets   "Download as Google Sheet" (phase0_foundations/gsheet_export.py)
    --feature inbox    SOP 1 statement drop-folder, read-only (phase0_foundations/drive_inbox.py)
    --feature gmail    Report email delivery, no app password (phase0_foundations/gmail_send.py)

Each feature authorizes under *your own* Google account (never the app's
service account -- see gsheet_export.py's module docstring for why a bare
service account can't be used here) and keeps its own token file, so the
three grants never share or clobber each other.

Run once, locally, from a machine with a browser:

    python scripts/google_oauth_setup.py                   # sheets (default)
    python scripts/google_oauth_setup.py --feature inbox
    python scripts/google_oauth_setup.py --feature gmail

It opens a browser for you to sign in and approve, then saves a refreshable
token to that feature's token file (env var overridable; see FEATURES below).
The running app reads that file and refreshes it silently from then on -- no
browser needed again unless you revoke access or delete the token file.

Requires GOOGLE_OAUTH_CLIENT_JSON to point at an OAuth 2.0 client secret
downloaded from Google Cloud Console (the same client works for all three
features):
    APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
    -> Application type: Desktop app -> Download JSON

The gmail feature additionally requires the Gmail API to be enabled on that
Cloud project (APIs & Services -> Library -> Gmail API -> Enable) and the
consent screen to allow the gmail.send scope.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from phase0_foundations.drive_inbox import SCOPE_READONLY  # noqa: E402
from phase0_foundations.gmail_send import SCOPE_GMAIL_SEND  # noqa: E402
from phase0_foundations.gsheet_export import SCOPES as SHEETS_SCOPES  # noqa: E402

load_dotenv()

# feature -> (scopes, token-path env var, default token filename, human label)
FEATURES: dict[str, tuple[list[str], str, str, str]] = {
    "sheets": (
        SHEETS_SCOPES, "GOOGLE_OAUTH_TOKEN_JSON", "google_oauth_token.json",
        "Download as Google Sheet",
    ),
    "inbox": (
        [SCOPE_READONLY], "GOOGLE_OAUTH_INBOX_TOKEN_JSON", "google_oauth_inbox_token.json",
        "SOP 1 statement drop-folder (Drive, read-only)",
    ),
    "gmail": (
        [SCOPE_GMAIL_SEND], "GOOGLE_OAUTH_GMAIL_TOKEN_JSON", "google_oauth_gmail_token.json",
        "Report email delivery (Gmail send)",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--feature", choices=sorted(FEATURES), default="sheets",
        help="Which integration to authorize (default: sheets).",
    )
    args = parser.parse_args()
    scopes, token_env, default_token_path, label = FEATURES[args.feature]

    client_json = os.environ.get("GOOGLE_OAUTH_CLIENT_JSON")
    if not client_json:
        raise SystemExit(
            "Set GOOGLE_OAUTH_CLIENT_JSON in .env to your downloaded OAuth "
            "client secret file first (see this script's docstring)."
        )
    if not os.path.exists(client_json):
        raise SystemExit(f"GOOGLE_OAUTH_CLIENT_JSON points at a file that doesn't exist: {client_json}")

    token_path = os.environ.get(token_env, default_token_path)

    flow = InstalledAppFlow.from_client_secrets_file(client_json, scopes=scopes)
    creds = flow.run_local_server(port=0)

    Path(token_path).write_text(creds.to_json(), encoding="utf-8")
    print(f"Authorized ({label}). Token saved to {token_path}.")
    print("The app will use this automatically from now on.")


if __name__ == "__main__":
    main()
