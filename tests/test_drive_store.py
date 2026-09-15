"""Tests for phase0_foundations/drive_store.py (Drive-backed watcher state)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from phase0_foundations import drive_store
from phase0_foundations.drive_store import fetch_tunnel_url, pull_dir, push_dir, push_tunnel_url


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _FakeDrive:
    """In-memory fake of the Drive files API surface drive_store uses."""

    def __init__(self):
        self.files_dict: dict[str, dict] = {}   # id -> metadata
        self.content: dict[str, bytes] = {}
        self.next_id = 0
        self.folder_id: str | None = None

    def _add(self, name, content, parent=None, folder=False):
        self.next_id += 1
        fid = f"id{self.next_id}"
        self.files_dict[fid] = {
            "id": fid, "name": name, "mimeType": (
                "application/vnd.google-apps.folder" if folder else "application/octet-stream"
            ),
            "md5Checksum": _md5(content) if content is not None else None,
        }
        self.content[fid] = content
        return fid

    # ---- service surface ----
    def files(self):
        return self

    def list(self, q="", fields="", pageSize=1000):
        self._q = q
        return self

    def execute(self):
        q = getattr(self, "_q", "")
        names = []
        if "name = 'cash-watcher-state'" in q:
            for f in self.files_dict.values():
                if f["name"] == "cash-watcher-state":
                    names.append({"id": f["id"], "name": f["name"]})
            return {"files": names}
        if "in parents" in q:
            for f in self.files_dict.values():
                names.append({"id": f["id"], "name": f["name"],
                              "md5Checksum": f["md5Checksum"], "mimeType": f["mimeType"]})
            return {"files": names}
        return {"files": []}

    def get_media(self, fileId=""):
        return _Req(lambda: self.content.get(fileId, b""))

    def create(self, body=None, media_body=None, fields=""):
        def _do():
            folder = body.get("mimeType") == "application/vnd.google-apps.folder"
            content = media_body.getbytes() if media_body is not None else None
            new_id = self._add(body.get("name"), content, folder=folder)
            if folder:
                self.folder_id = new_id
            return {"id": new_id}
        return _Req(_do)

    def update(self, fileId="", media_body=None):
        def _do():
            content = media_body.getbytes()
            self.content[fileId] = content
            self.files_dict[fileId]["md5Checksum"] = _md5(content)
            return {"id": fileId}
        return _Req(_do)


@pytest.fixture
def drive(monkeypatch):
    fake = _FakeDrive()
    monkeypatch.setattr(drive_store, "get_drive_service", lambda: fake)
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    return fake


def test_ensure_folder_creates_once_and_reuses(drive, tmp_path):
    folder1 = drive_store._ensure_sync_folder(drive)
    folder2 = drive_store._ensure_sync_folder(drive)
    assert folder1 == folder2
    assert drive.folder_id == folder1


def test_pull_downloads_missing_and_skips_unchanged(drive, tmp_path):
    state_bytes = b'{"borrowers": {"leasy": {}}}'
    drive._add("cash_watcher_state.json", state_bytes)
    (tmp_path / "cash_watcher_state.json").write_bytes(state_bytes)  # already current

    pulled = pull_dir(tmp_path)
    assert pulled == 0  # local md5 matches -> nothing to pull
    assert (tmp_path / "cash_watcher_state.json").read_bytes() == state_bytes

    # add a report missing locally -> pulled
    drive._add("leasy/run_x.docx", b"docx")
    assert pull_dir(tmp_path) == 1
    assert (tmp_path / "leasy" / "run_x.docx").read_bytes() == b"docx"


def test_pull_ignores_traversal_names(drive, tmp_path):
    drive._add("../evil", b"nope")
    drive._add("/abs", b"nope")
    assert pull_dir(tmp_path) == 0
    assert not (tmp_path.parent / "evil").exists()
    assert not (tmp_path / "abs").exists()


def test_push_uploads_new_updates_changed_skips_same(drive, tmp_path, monkeypatch):
    class _FakeMedia:
        def __init__(self, path, mimetype="application/octet-stream"):
            self.path = path

        def getbytes(self):
            return Path(self.path).read_bytes()

    monkeypatch.setattr(drive_store, "MediaFileUpload", _FakeMedia)
    (tmp_path / "cash_watcher_state.json").write_bytes(b'{"borrowers": {}}')
    (tmp_path / "leasy").mkdir(parents=True, exist_ok=True)
    (tmp_path / "leasy" / "run_x.docx").write_bytes(b"v1")

    # first push: both files new
    assert push_dir(tmp_path) == 2
    # second push: unchanged -> nothing
    assert push_dir(tmp_path) == 0
    # change a file -> updated, not duplicated
    (tmp_path / "leasy" / "run_x.docx").write_bytes(b"v2")
    assert push_dir(tmp_path) == 1
    names = [f["name"] for f in drive.files_dict.values()]
    assert names.count("leasy/run_x.docx") == 1
    updated = [f for f in drive.files_dict.values() if f["name"] == "leasy/run_x.docx"][0]
    assert drive.content[updated["id"]] == b"v2"


class _FakeMediaMem:
    def __init__(self, payload, mimetype="application/json"):
        self.payload = payload

    def getbytes(self):
        return self.payload


def test_fetch_tunnel_url_none_when_never_published(drive):
    assert fetch_tunnel_url() is None


def test_push_tunnel_url_then_fetch_round_trips(drive, monkeypatch):
    monkeypatch.setattr(drive_store, "MediaInMemoryUpload", _FakeMediaMem)

    push_tunnel_url("https://one.trycloudflare.com")
    assert fetch_tunnel_url() == "https://one.trycloudflare.com"


def test_push_tunnel_url_updates_in_place_not_duplicated(drive, monkeypatch):
    monkeypatch.setattr(drive_store, "MediaInMemoryUpload", _FakeMediaMem)

    push_tunnel_url("https://one.trycloudflare.com")
    push_tunnel_url("https://two.trycloudflare.com")

    assert fetch_tunnel_url() == "https://two.trycloudflare.com"
    matches = [f for f in drive.files_dict.values() if f["name"] == "redshift_tunnel_url.json"]
    assert len(matches) == 1


def test_fetch_tunnel_url_returns_none_on_any_failure(monkeypatch):
    def _boom():
        raise RuntimeError("no credentials available")

    monkeypatch.setattr(drive_store, "get_drive_service", _boom)
    assert fetch_tunnel_url() is None
