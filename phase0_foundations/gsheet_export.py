"""Export tabular data to a native Google Sheet, created under your own
Google account rather than a service account's.

A bare service account has zero Drive storage quota of its own and owns
every file it creates, so it can only write into a Shared Drive (pooled
storage) -- not a regular "My Drive" folder, even one shared with it. Since
Shared Drives aren't available here, this uses a normal OAuth user-consent
flow instead: you authorize the app once (via `scripts/google_oauth_setup.py`,
run interactively, once, outside the running app), and it saves a refreshable
token this module then uses silently at runtime. Files land in your own
Drive, under your own quota, in any folder you already have access to.

No separate Sheets API call is needed: Drive's `files.create` accepts a CSV
upload with a target `mimeType` of `application/vnd.google-apps.spreadsheet`,
which server-side-converts it into a native, editable Sheet in one call.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# drive.file: the app can only see/manage files *it* creates -- never a
# standing grant over your whole Drive. Keep in sync with
# scripts/google_oauth_setup.py, which requests the same scope.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Cache of (token file path) -> Drive service, so it's built once per process.
_service_cache: dict[str, Any] = {}


class GSheetExportError(RuntimeError):
    """Raised when authorization is missing/invalid, or the Drive API call fails."""


def _token_path() -> str:
    return os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "google_oauth_token.json")


def get_drive_service():
    path = _token_path()
    if path in _service_cache:
        return _service_cache[path]

    if not os.path.exists(path):
        raise GSheetExportError(
            f"No Google authorization found at '{path}'. Run "
            "`python scripts/google_oauth_setup.py` once to sign in with your "
            "own Google account before exporting."
        )

    try:
        creds = UserCredentials.from_authorized_user_file(path, SCOPES)
    except (OSError, ValueError) as exc:
        raise GSheetExportError(f"Could not read Google authorization at '{path}': {exc}") from exc

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:  # noqa: BLE001 - surface refresh failures clearly
                raise GSheetExportError(
                    f"Google authorization at '{path}' could not be refreshed ({exc}). "
                    "Re-run `python scripts/google_oauth_setup.py` to sign in again."
                ) from exc
            Path(path).write_text(creds.to_json(), encoding="utf-8")
        else:
            raise GSheetExportError(
                f"Google authorization at '{path}' is invalid or was revoked. Re-run "
                "`python scripts/google_oauth_setup.py` to sign in again."
            )

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    _service_cache[path] = service
    return service


def _rows_to_csv_bytes(columns: list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def export_rows_as_gsheet(
    name: str,
    columns: list[str],
    rows: list[list[Any]],
    *,
    folder_id: str | None = None,
) -> str:
    """Create a native Google Sheet from `columns`/`rows` and return its URL.

    `folder_id` defaults to the `DRIVE_FOLDER_ID` env var if set; both are
    optional -- with neither, the Sheet is created in your Drive root ("My
    Drive"), which is fine since it's created under your own account and is
    immediately visible to you.
    """
    target_folder = folder_id or os.environ.get("DRIVE_FOLDER_ID")

    service = get_drive_service()
    csv_bytes = _rows_to_csv_bytes(columns, rows)
    media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype="text/csv", resumable=False)
    body: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    if target_folder:
        body["parents"] = [target_folder]

    try:
        created = service.files().create(
            body=body, media_body=media, fields="id, webViewLink", supportsAllDrives=True,
        ).execute()
    except Exception as exc:  # noqa: BLE001 - surface the Drive API error to the caller
        raise GSheetExportError(f"Google Sheets export failed: {exc}") from exc

    return created.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{created['id']}/edit"
