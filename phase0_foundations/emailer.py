"""SOP 1 report email delivery — Gmail API (OAuth), no password.

Composes the watcher's working paper (.md + .json) as a MIME message and sends
it via the Gmail API using the project's existing Google OAuth pattern
(phase0_foundations/gmail_send.py). Chosen over SMTP app passwords because
corporate Google Workspace accounts commonly have app passwords disabled by the
domain admin.

One-time setup: `python scripts/google_oauth_setup_gmail.py` (grants gmail.send).
The token is read from .env at send time via `gmail_token_env`; recipients/from/
subject are data-driven config. Disabled by default and only meaningful after a
real authenticated send is verified (repo rule: no external integration goes
live on configuration alone).
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from phase0_foundations.config import EmailConfig
from phase0_foundations.gmail_send import GmailSendError, send_raw_message

_SUBTYPES = {
    ".json": ("application", "json"),
    ".md": ("text", "markdown"),
    ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".pdf": ("application", "pdf"),
}


def send_report_email(
    cfg: EmailConfig, report_paths: list[Path], subject: str,
    body_html: str | None = None,
) -> bool:
    """Send the report files to the configured recipients via the Gmail API.

    ``body_html`` (optional) is a well-typed HTML body; when given it is used
    as the email body (with a short plain-text fallback). Returns True on
    success. Returns False without sending when the feature is not configured
    (disabled / no recipients / no from). Raises on a genuine send failure so
    the caller can log the reason — a mail outage must never be silently
    swallowed as "sent".
    """
    if not cfg.enabled or not cfg.to_addrs or not cfg.from_addr:
        return False

    msg = EmailMessage()
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg["Subject"] = f"{cfg.subject_prefix}{subject}"
    msg["Date"] = formatdate(localtime=True)
    if body_html:
        msg.set_content("Attached: verification working paper(s). See the .docx for the full report.")
        msg.add_alternative(body_html, subtype="html")
    else:
        msg.set_content("Attached: verification working paper(s).\n")
    attached = 0
    for p in report_paths:
        p = Path(p)
        if not p.exists():
            continue
        maintype, subtype = _SUBTYPES.get(p.suffix.lower(), ("application", "octet-stream"))
        msg.add_attachment(
            p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name,
        )
        attached += 1
    if attached == 0:
        return False

    try:
        send_raw_message(msg.as_bytes())
    except GmailSendError as exc:
        raise RuntimeError(f"Gmail send failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - surface any transport failure
        raise RuntimeError(f"Gmail send failed: {exc}") from exc
    return True
