"""Tests for phase0_foundations/gmail_send.py (Gmail API OAuth transport)."""

from __future__ import annotations

import base64
import json

import pytest

from phase0_foundations import gmail_send
from phase0_foundations.gmail_send import GmailSendError, get_gmail_service, send_raw_message


def test_get_service_raises_when_token_missing(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_GMAIL_TOKEN_JSON", "/nonexistent/token.json")
    with pytest.raises(GmailSendError, match="google_oauth_setup_gmail"):
        get_gmail_service()


def test_get_service_refreshes_expired_token(monkeypatch, tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"token": "x", "refresh_token": "r", "expiry": "2020-01-01T00:00:00Z"}),
                     encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_GMAIL_TOKEN_JSON", str(token))

    class FakeCreds:
        def __init__(self):
            self.valid = False
            self.expired = True
            self.refresh_token = "r"

        def refresh(self, request):
            self.valid = True

        def to_json(self):
            return '{"token": "refreshed"}'

    class FakeRequest:
        pass

    monkeypatch.setattr(gmail_send.UserCredentials, "from_authorized_user_file",
                        lambda path, scopes: FakeCreds())
    monkeypatch.setattr("phase0_foundations.gmail_send.Request", FakeRequest)
    built = {}

    def fake_build(api, version, credentials=None, cache_discovery=False):
        built["creds"] = credentials
        return object()

    monkeypatch.setattr("phase0_foundations.gmail_send.build", fake_build)
    get_gmail_service()
    assert built.get("creds") is not None
    # token file refreshed
    assert "refreshed" in token.read_text(encoding="utf-8")


def test_send_raw_message_calls_gmail_api(monkeypatch):
    sent = {}

    class FakeSend:
        def __init__(self, body):
            sent["body"] = body

        def execute(self):
            return {"id": "msg123"}

    class FakeMessages:
        def send(self, userId, body):
            return FakeSend(body)

    class FakeUsers:
        def messages(self):
            return FakeMessages()

    class FakeService:
        def users(self):
            return FakeUsers()

    monkeypatch.setattr(gmail_send, "get_gmail_service", lambda: FakeService())
    raw = b"From: a@b.com\r\nSubject: hi\r\n\r\nbody"
    send_raw_message(raw)
    decoded = base64.urlsafe_b64decode(sent["body"]["raw"].encode("ascii"))
    assert decoded == raw
