"""Review workflow (A3 steps 7–8).

State machine:
  pending -> reviewed (Verification Officer begins) -> approved | rejected

Sign-off is a final, separate gate (Head of Risk). Every transition is appended
to the run log so the workflow record's "what gets logged" guarantee holds.

Actions are authoritative: approval/rejection are recorded as decisions, not
recommendations from the LLM.
"""

from __future__ import annotations

from phase0_foundations.log import RunLog
from phase0_foundations.models import ExceptionItem, ReviewAction

STATUS_PENDING = "pending"
STATUS_REVIEWED = "reviewed"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


def _transition(
    item: ExceptionItem,
    action: str,
    reviewer: str,
    note: str,
    log: RunLog | None,
) -> ReviewAction:
    if item.status not in (STATUS_PENDING, STATUS_REVIEWED):
        raise ValueError(f"Item {item.id} is {item.status}; cannot {action}.")
    item.status = STATUS_APPROVED if action == "approved" else STATUS_REJECTED
    item.reviewer = reviewer
    item.review_note = note
    ra = ReviewAction(
        exception_id=item.id,
        action=action,
        reviewer=reviewer,
        note=note,
    )
    if log is not None:
        log.append(
            {
                "event": "review",
                "exception_id": item.id,
                "action": action,
                "reviewer": reviewer,
                "note": note,
                "resulting_status": item.status,
            }
        )
    return ra


def review_start(item: ExceptionItem, reviewer: str, note: str = "", log: RunLog | None = None) -> None:
    """Verification Officer takes ownership of an exception for a deep-dive."""
    if item.status != STATUS_PENDING:
        raise ValueError(f"Item {item.id} is {item.status}; expected {STATUS_PENDING}.")
    item.status = STATUS_REVIEWED
    item.reviewer = reviewer
    item.review_note = note
    if log is not None:
        log.append(
            {
                "event": "review_start",
                "exception_id": item.id,
                "reviewer": reviewer,
                "note": note,
            }
        )


def approved(item: ExceptionItem, reviewer: str, note: str = "", log: RunLog | None = None) -> ReviewAction:
    return _transition(item, "approved", reviewer, note, log)


def reject(item: ExceptionItem, reviewer: str, note: str = "", log: RunLog | None = None) -> ReviewAction:
    return _transition(item, "rejected", reviewer, note, log)


def sign_off(item: ExceptionItem, signer: str, appr: bool, log: RunLog | None = None) -> None:
    """Final Head-of-Risk gate. `appr=True` closes sign-off, `False` reopens."""
    if item.status not in (STATUS_APPROVED, STATUS_REJECTED):
        raise ValueError(f"Item {item.id} must be decided before sign-off.")
    if log is not None:
        log.append(
            {
                "event": "sign_off",
                "exception_id": item.id,
                "signer": signer,
                "approved": appr,
                "status": item.status,
            }
        )
