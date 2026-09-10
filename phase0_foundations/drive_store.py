"""Drive-backed persistence for the watcher's state + audit trail.

GitHub-hosted runners are ephemeral (a fresh filesystem every run), so the
watcher's durable state (`cash_watcher_state.json`: watermarks, ingested
statement fingerprints, last-run timestamps) and its retained statement files
would reset on every run — every poll would re-detect every borrower and
re-email endlessly. This module mirrors the watcher's `out/` directory to an
app-created Google Drive folder using the existing `drive.file`-scoped OAuth
token (the same one gsheet_export.py uses: the app can only see/manage files
it created itself).

Used by `cash_watcher.py --drive-sync`: pull before a poll (restore state from
the previous run) and push after (persist this run's state + reports for the
next). Tolerant by design: every operation is a best-effort sync and the
watcher treats a sync failure as logged-and-continue, never fatal.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from googleapiclient.http import MediaFileUpload

from phase0_foundations.gsheet_export import get_drive_service

_FOLDER_NAME = "cash-watcher-state"
_IGNORE = {"cash_watcher_state.json.tmp"}


class DriveStoreError(RuntimeError):
    """Raised when the Drive sync folder cannot be resolved/used."""


def _ensure_sync_folder(service) -> str:
    """Find the app's 'cash-watcher-state' folder by name (created by the app,
    so drive.file scope allows it), creating it under DRIVE_FOLDER_ID (or My
    Drive root) on first use. Looked up by name rather than remembered in a
    marker file so an ephemeral runner can re-find the same folder each run."""
    q = (
        f"name = '{_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    res = service.files().list(q=q, fields="files(id, name)", pageSize=10).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    parent = os.environ.get("DRIVE_FOLDER_ID")
    body = {"name": _FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        body["parents"] = [parent]
    created = service.files().create(body=body, fields="id").execute()
    return created["id"]


def _list_files(service, folder_id: str) -> list[dict]:
    res = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="files(id, name, md5Checksum, mimeType)",
        pageSize=1000,
    ).execute()
    return res.get("files", [])


def _safe_rel_name(name: str) -> bool:
    """Reject names that could escape the sync root (Drive names are flat, so
    subdirectories are encoded as '/' inside the name)."""
    return bool(name) and not name.startswith("/") and ".." not in name


def _local_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def pull_dir(out_root: str | Path) -> int:
    """Download files from the Drive sync folder into ``out_root`` when the
    local copy is missing or differs (by content md5). Returns files pulled.
    Raises DriveStoreError on auth/API failure (caller decides: log + continue)."""
    root = Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    service = get_drive_service()
    folder = _ensure_sync_folder(service)
    pulled = 0
    for f in _list_files(service, folder):
        name = f.get("name") or ""
        if not _safe_rel_name(name) or f.get("mimeType") == "application/vnd.google-apps.folder":
            continue
        local = root / name
        remote_md5 = f.get("md5Checksum")
        if local.exists() and remote_md5 and _local_md5(local) == remote_md5:
            continue
        data = service.files().get_media(fileId=f["id"]).execute()
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        pulled += 1
    return pulled


def push_dir(out_root: str | Path) -> int:
    """Upload files under ``out_root`` to the Drive sync folder when missing or
    changed (by content md5), creating/updating as needed. Returns files pushed.
    Raises DriveStoreError on auth/API failure."""
    root = Path(out_root)
    service = get_drive_service()
    folder = _ensure_sync_folder(service)
    remote = {f["name"]: f for f in _list_files(service, folder)}
    pushed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not _safe_rel_name(rel) or rel in _IGNORE:
            continue
        existing = remote.get(rel)
        if existing and existing.get("md5Checksum") == _local_md5(path):
            continue
        media = MediaFileUpload(str(path), mimetype="application/octet-stream")
        if existing:
            service.files().update(fileId=existing["id"], media_body=media).execute()
        else:
            service.files().create(
                body={"name": rel, "parents": [folder]}, media_body=media, fields="id",
            ).execute()
        pushed += 1
    return pushed
