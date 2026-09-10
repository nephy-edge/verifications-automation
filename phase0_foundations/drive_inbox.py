"""Read-only Google Drive inbox reader for the SOP 1 statement drop-folder.

Humans drop bank/wallet statement files (PDF/CSV/XLSX) into a designated Drive
folder per borrower; the watcher (cash_watcher.py) reads any *new* files here on
each cycle, parses them as the independent bank/mobile side of reconciliation,
and retains the raw files as the durable audit trail.

Auth model: this uses `drive.readonly` — a *separate*, intentionally read-only
scope with its own OAuth token, distinct from `gsheet_export.py`'s `drive.file`
scope (which only sees files the app itself created and cannot list a folder
another party uploads to). To read a dropped-statement inbox you must grant the
broader read scope once:

    python scripts/google_oauth_setup_inbox.py   # one-time, opens browser

The resulting token is stored at `GOOGLE_OAUTH_INBOX_TOKEN_JSON`
(default: google_oauth_inbox_token.json) and refreshed silently at runtime.
Without it the module raises DriveInboxError on first use; the watcher logs the
missing setup as a degraded (tape-only) run rather than crashing.

Because the scope is read-only, the module cannot move or create folders.
Idempotency ("a file is picked once") is the *caller's* responsibility: the
watcher records each (file_id, modifiedTime) it has already ingested in its state
file, so re-downloading a file it has processed is skipped without mutating Drive.
The raw files stay in place, untouched — which is also a cleaner audit trail.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build

# Read-only — only ever lists/downloads; never creates, edits or moves content.
SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

# Cache of (token path) -> service, built once per process.
_service_cache: dict[str, object] = {}


class DriveInboxError(RuntimeError):
    """Raised when Drive inbox auth is missing/invalid, or the Drive API fails."""


@dataclass(frozen=True)
class InboxFile:
    """A statement file found in the inbox. `fingerprint` is a stable identity
    the caller can use to track which files it has already ingested."""
    file_id: str
    name: str
    mime: str
    modified_at: str

    @property
    def fingerprint(self) -> str:
        """File identity + content version: `(file_id, modifiedAt)` so a file
        that is *re-uploaded/edited* (new modifiedAt) is treated as new."""
        return f"{self.file_id}@{self.modified_at}"


def _token_path() -> str:
    return os.environ.get("GOOGLE_OAUTH_INBOX_TOKEN_JSON", "google_oauth_inbox_token.json")


def get_drive_service():
    """Build (and cache) an authenticated Drive service with read-only scope."""
    path = _token_path()
    if path in _service_cache:
        return _service_cache[path]

    if not os.path.exists(path):
        raise DriveInboxError(
            f"No read-only Google authorization found at '{path}'. Statements will "
            "not be fetched from Drive. Run `python scripts/google_oauth_setup_inbox.py` "
            "once to sign in with your Google account (grants drive.readonly so the "
            "app can list/download your dropped statements)."
        )
    try:
        creds = UserCredentials.from_authorized_user_file(path, [SCOPE_READONLY])
    except (OSError, ValueError) as exc:
        raise DriveInboxError(f"Could not read Drive inbox authorization '{path}': {exc}") from exc

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:  # noqa: BLE001 - surface refresh failures clearly
                raise DriveInboxError(
                    f"Drive inbox token at '{path}' could not be refreshed ({exc}). "
                    "Re-run `python scripts/google_oauth_setup_inbox.py`."
                ) from exc
            Path(path).write_text(creds.to_json(), encoding="utf-8")
        else:
            raise DriveInboxError(
                f"Drive inbox authorization at '{path}' is invalid/revoked. "
                "Re-run `python scripts/google_oauth_setup_inbox.py`."
            )

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    _service_cache[path] = service
    return service


def _resolve_folder_id(service, folder: str) -> str:
    """Resolve `folder` to a Drive folder id. A long alphanumeric string is
    treated as a raw folder/room id; anything else is treated as a folder *name*
    under the user's Drive root (so a shared `Bank Statements` folder can be
    targeted by name)."""
    if len(folder) >= 20 and folder.replace("_", "").replace("-", "").isalnum():
        return folder
    query = (
        f"name = '{folder}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    res = service.files().list(q=query, fields="files(id, name)", pageSize=10).execute()
    files = res.get("files", [])
    if not files:
        raise DriveInboxError(f"Drive inbox folder '{folder}' not found.")
    return files[0]["id"]


def list_new_statements(folder: str) -> list[InboxFile]:
    """List statement files currently in the inbox folder, newest-first by
    modified time. Only PDF/CSV/TSV/XLSX/XLS files are returned."""
    service = get_drive_service()
    fid = _resolve_folder_id(service, folder)
    query = (
        f"'{fid}' in parents and trashed = false "
        "and mimeType != 'application/vnd.google-apps.folder'"
    )
    res = service.files().list(
        q=query,
        fields="files(id, name, mimeType, modifiedTime, createdTime)",
        pageSize=300,
    ).execute()

    supported = {".pdf", ".csv", ".tsv", ".xlsx", ".xls"}
    out: list[InboxFile] = []
    for f in res.get("files", []):
        name = f.get("name", "")
        if not name or Path(name).suffix.lower() not in supported:
            continue
        out.append(
            InboxFile(
                file_id=f["id"],
                name=name,
                mime=f.get("mimeType", ""),
                modified_at=f.get("modifiedTime") or f.get("createdTime") or "",
            )
        )
    out.sort(key=lambda f: f.modified_at, reverse=True)
    return out


def download_file(file_id: str, name: str) -> bytes:
    """Download a Drive file's raw bytes. Exports Google-native types
    (Sheets -> CSV, text -> plain) and streams large binaries to memory."""
    service = get_drive_service()
    lower = Path(name).suffix.lower()
    if lower == ".pdf":
        req = service.files().get_media(fileId=file_id)
        return _stream(req)
    if lower in (".xlsx", ".xls"):
        return _stream(service.files().get_media(fileId=file_id))
    if lower in (".csv", ".tsv"):
        # Google native Sheets are stored as app-spreadsheet; export to CSV.
        try:
            meta = service.files().get(fileId=file_id, fields="mimeType").execute()
        except Exception:  # noqa: BLE001
            meta = {"mimeType": ""}
        if meta.get("mimeType") == "application/vnd.google-apps.spreadsheet":
            response = service.files().export(
                fileId=file_id, mimeType="text/csv"
            ).execute()
            return response
        return _stream(service.files().get_media(fileId=file_id))
    # Fallback: raw binary download.
    return _stream(service.files().get_media(fileId=file_id))


def _stream(req) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def sanity_check() -> str:
    service = get_drive_service()
    about = service.about().get(fields="user(emailAddress)").execute()
    return about.get("user", {}).get("emailAddress", "unknown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
