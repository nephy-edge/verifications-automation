"""Tests for SOP 1 report email delivery (phase0_foundations/emailer.py).

The Gmail API transport (phase0_foundations/gmail_send.py) is faked so tests
run offline; the real send path is verified live (per repo rule: external
integrations go live only after a real authenticated send).
"""

from __future__ import annotations

import pytest

from phase0_foundations.config import EmailConfig
from phase0_foundations.emailer import send_report_email
from phase0_foundations.gmail_send import GmailSendError


def test_email_config_default_disabled():
    cfg = EmailConfig()
    assert cfg.enabled is False
    assert cfg.gmail_token_env == "GOOGLE_OAUTH_GMAIL_TOKEN_JSON"
    assert cfg.to_addrs == []


def test_email_config_from_dict():
    cfg = EmailConfig.from_dict(
        {
            "enabled": True,
            "gmail_token_env": "CUSTOM_TOKEN",
            "from_addr": "a@b.com",
            "to_addrs": ["x@y.com", "z@w.com"],
            "subject_prefix": "[X] ",
        }
    )
    assert cfg.enabled is True
    assert cfg.gmail_token_env == "CUSTOM_TOKEN"
    assert cfg.to_addrs == ["x@y.com", "z@w.com"]
    assert cfg.subject_prefix == "[X] "


def test_send_returns_false_when_disabled():
    cfg = EmailConfig(enabled=False, from_addr="a@b.com", to_addrs=["x@y.com"])
    assert send_report_email(cfg, [], "subject") is False


def test_send_returns_false_without_recipients_or_from():
    cfg = EmailConfig(enabled=True, from_addr="", to_addrs=[])
    assert send_report_email(cfg, [], "subject") is False
    cfg2 = EmailConfig(enabled=True, from_addr="a@b.com", to_addrs=[])
    assert send_report_email(cfg2, [], "subject") is False


def test_send_success_delivers_raw_message(monkeypatch, tmp_path):
    from email import policy
    from email.parser import BytesParser

    captured: dict = {}

    def fake_send(raw: bytes):
        captured["raw"] = raw

    monkeypatch.setattr("phase0_foundations.emailer.send_raw_message", fake_send)

    md = tmp_path / "run_x.md"
    js = tmp_path / "run_x.json"
    dx = tmp_path / "run_x.docx"
    md.write_text("# report", encoding="utf-8")
    js.write_text("{}", encoding="utf-8")
    dx.write_bytes(b"PK\x03\x04fake-docx")

    cfg = EmailConfig(enabled=True, from_addr="nephy@lendable.io", to_addrs=["nephy@lendable.io"])
    body = "<html><body><h2>Verification Report</h2></body></html>"
    assert send_report_email(cfg, [dx, js, md], "leasy verification run x",
                             body_html=body) is True

    msg = BytesParser(policy=policy.default).parsebytes(captured["raw"])
    assert msg["Subject"] == "[Verifications] leasy verification run x"
    assert msg["To"] == "nephy@lendable.io"
    # the HTML body is present as an alternative part
    html_parts = [p for p in msg.walk() if p.get_content_type() == "text/html"]
    assert html_parts and "Verification Report" in html_parts[0].get_content()
    # all three files attached, with the correct docx MIME type
    payload = list(msg.iter_attachments())
    names = {p.get_filename() for p in payload}
    assert names == {"run_x.md", "run_x.json", "run_x.docx"}
    docx_part = [p for p in payload if p.get_filename() == "run_x.docx"][0]
    assert docx_part.get_content_type() == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_send_skips_missing_files(monkeypatch, tmp_path):
    called = {"n": 0}

    def fake_send(raw: bytes):
        called["n"] += 1

    monkeypatch.setattr("phase0_foundations.emailer.send_raw_message", fake_send)
    cfg = EmailConfig(enabled=True, from_addr="a@b.com", to_addrs=["x@y.com"])
    # both report files missing -> nothing to attach -> False, no API call
    assert send_report_email(cfg, [tmp_path / "nope.md", tmp_path / "nope.json"], "s") is False
    assert called["n"] == 0


def test_send_raises_on_gmail_failure(monkeypatch, tmp_path):
    def boom(raw: bytes):
        raise GmailSendError("token missing - run google_oauth_setup_gmail.py")

    monkeypatch.setattr("phase0_foundations.emailer.send_raw_message", boom)
    md = tmp_path / "run_x.md"
    md.write_text("# report", encoding="utf-8")
    cfg = EmailConfig(enabled=True, from_addr="a@b.com", to_addrs=["x@y.com"])
    with pytest.raises(RuntimeError, match="token missing"):
        send_report_email(cfg, [md], "s")
