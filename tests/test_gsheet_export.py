"""Tests for phase0_foundations/gsheet_export.py — the Drive service and
credentials are mocked throughout; nothing here makes a real Google API
call or touches a real token file."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations import gsheet_export  # noqa: E402


def test_rows_to_csv_bytes_includes_header_and_rows():
    csv_bytes = gsheet_export._rows_to_csv_bytes(["a", "b"], [[1, "x"], [2, "y"]])
    text = csv_bytes.decode("utf-8")
    assert text.splitlines() == ["a,b", "1,x", "2,y"]


def test_get_drive_service_raises_when_token_file_missing(monkeypatch, tmp_path):
    missing = tmp_path / "no-such-token.json"
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_JSON", str(missing))
    gsheet_export._service_cache.clear()
    try:
        gsheet_export.get_drive_service()
        raise AssertionError("expected GSheetExportError")
    except gsheet_export.GSheetExportError as exc:
        assert "google_oauth_setup.py" in str(exc)


def test_get_drive_service_refreshes_expired_token(monkeypatch, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_JSON", str(token_path))
    gsheet_export._service_cache.clear()

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "refresh-me"
    fake_creds.to_json.return_value = '{"refreshed": true}'

    def _mark_valid_on_refresh(_request):
        fake_creds.valid = True

    fake_creds.refresh.side_effect = _mark_valid_on_refresh

    with patch.object(gsheet_export.UserCredentials, "from_authorized_user_file", return_value=fake_creds):
        with patch.object(gsheet_export, "build", return_value=MagicMock()) as mock_build:
            gsheet_export.get_drive_service()

    fake_creds.refresh.assert_called_once()
    assert token_path.read_text(encoding="utf-8") == '{"refreshed": true}'
    mock_build.assert_called_once()


def test_get_drive_service_raises_when_refresh_fails(monkeypatch, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_JSON", str(token_path))
    gsheet_export._service_cache.clear()

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "refresh-me"
    fake_creds.refresh.side_effect = RuntimeError("token revoked")

    with patch.object(gsheet_export.UserCredentials, "from_authorized_user_file", return_value=fake_creds):
        try:
            gsheet_export.get_drive_service()
            raise AssertionError("expected GSheetExportError")
        except gsheet_export.GSheetExportError as exc:
            assert "google_oauth_setup.py" in str(exc)


def test_get_drive_service_raises_when_no_refresh_token(monkeypatch, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_JSON", str(token_path))
    gsheet_export._service_cache.clear()

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = None

    with patch.object(gsheet_export.UserCredentials, "from_authorized_user_file", return_value=fake_creds):
        try:
            gsheet_export.get_drive_service()
            raise AssertionError("expected GSheetExportError")
        except gsheet_export.GSheetExportError as exc:
            assert "google_oauth_setup.py" in str(exc)


def test_export_rows_as_gsheet_calls_drive_api_with_sheet_mimetype_and_folder():
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.return_value = {
        "id": "abc123",
        "webViewLink": "https://docs.google.com/spreadsheets/d/abc123/edit",
    }
    with patch.object(gsheet_export, "get_drive_service", return_value=fake_service):
        url = gsheet_export.export_rows_as_gsheet(
            "my_sheet", ["a", "b"], [[1, 2]], folder_id="folder-1",
        )
    assert url == "https://docs.google.com/spreadsheets/d/abc123/edit"

    _, kwargs = fake_service.files.return_value.create.call_args
    assert kwargs["body"] == {
        "name": "my_sheet",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": ["folder-1"],
    }
    assert kwargs["media_body"].mimetype() == "text/csv"
    assert kwargs["supportsAllDrives"] is True


def test_export_rows_as_gsheet_omits_parents_without_a_folder(monkeypatch):
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.return_value = {
        "id": "abc123", "webViewLink": "https://docs.google.com/spreadsheets/d/abc123/edit",
    }
    with patch.object(gsheet_export, "get_drive_service", return_value=fake_service):
        gsheet_export.export_rows_as_gsheet("my_sheet", ["a"], [[1]])
    _, kwargs = fake_service.files.return_value.create.call_args
    assert "parents" not in kwargs["body"]


def test_export_rows_as_gsheet_falls_back_to_env_folder(monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER_ID", "env-folder")
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.return_value = {
        "id": "xyz", "webViewLink": "https://docs.google.com/spreadsheets/d/xyz/edit",
    }
    with patch.object(gsheet_export, "get_drive_service", return_value=fake_service):
        gsheet_export.export_rows_as_gsheet("name", ["a"], [[1]])
    _, kwargs = fake_service.files.return_value.create.call_args
    assert kwargs["body"]["parents"] == ["env-folder"]


def test_export_constructs_url_when_webviewlink_missing():
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.return_value = {"id": "onlyid"}
    with patch.object(gsheet_export, "get_drive_service", return_value=fake_service):
        url = gsheet_export.export_rows_as_gsheet("name", ["a"], [[1]], folder_id="f")
    assert url == "https://docs.google.com/spreadsheets/d/onlyid/edit"


def test_export_wraps_drive_api_errors():
    fake_service = MagicMock()
    fake_service.files.return_value.create.return_value.execute.side_effect = RuntimeError("quota exceeded")
    with patch.object(gsheet_export, "get_drive_service", return_value=fake_service):
        try:
            gsheet_export.export_rows_as_gsheet("name", ["a"], [[1]], folder_id="f")
            raise AssertionError("expected GSheetExportError")
        except gsheet_export.GSheetExportError as exc:
            assert "quota exceeded" in str(exc)


if __name__ == "__main__":
    test_rows_to_csv_bytes_includes_header_and_rows()
    test_export_rows_as_gsheet_calls_drive_api_with_sheet_mimetype_and_folder()
    test_export_rows_as_gsheet_omits_parents_without_a_folder()
    test_export_constructs_url_when_webviewlink_missing()
    test_export_wraps_drive_api_errors()
    print("gsheet_export tests OK (run via pytest for the monkeypatch/tmp_path-dependent cases)")
