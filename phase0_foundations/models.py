"""Domain models for the verification workflow.

Plain dataclasses (no ORM), matching the minimal style used elsewhere in the
workspace. The `VerifyRecord` is a transaction-level row that aggregates across
all input types; exceptions reference it by a stable key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VerificationRun:
    """One end-to-end run of the workflow (A3 step)."""

    id: str | None = None
    status: str = "running"           # running | done | failed
    inputs: dict[str, Any] = field(default_factory=dict)
    aggregates: dict[str, Any] = field(default_factory=dict)
    exceptions: list[ExceptionItem] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class ExceptionItem:
    """A flagged discrepancy or anomaly for human review (A3 step 5)."""

    id: str
    kind: str                 # reconciliation | anomaly
    severity: float = 0.0     # ranked 0..1
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    status: str = "pending"   # pending | reviewed | approved | rejected
    reviewer: str | None = None
    review_note: str | None = None


@dataclass
class ReviewAction:
    """A human decision captured on an exception (A3 step 7)."""

    exception_id: str
    action: str               # approved | edited | rejected
    reviewer: str
    note: str = ""
    at: str | None = None
