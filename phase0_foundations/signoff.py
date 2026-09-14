"""Lightweight sign-off trail for verification run working papers.

Not a legally-binding e-signature (SOP 1's "dual-control login" and formal
bank-portal balance certification stay human/out-of-scope — see
docs/Verifications_Checklist.md). This closes the narrower, buildable gap:
a durable record of *who* reviewed and approved a run's working paper and
*when*, appended to the same audit log every other watcher/app event uses
(`phase0_foundations.log.RunLog` — "each run is logged; human signal drives
the next iteration"), plus a `run_<id>.signoff.json` file next to the report
itself so the sign-off travels with the report through Drive sync the same
way the report and statements already do.

The recorded SHA-256 of the report file at sign-off time lets a later check
(`verify_signoff`) detect if the report was regenerated/edited after being
signed off, without needing to make reports themselves immutable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from phase0_foundations.log import RunLog


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportNotFoundError(Exception):
    """No `run_<id>.json` exists for this borrower/run_id to sign off on."""


class AlreadySignedOffError(Exception):
    """This run already has a signer of record. The trail is append-only per
    run -- one signer, not silently overwritable -- so re-signing is refused
    rather than clobbering the existing record."""


@dataclass
class SignOffRecord:
    run_id: str
    borrower: str
    signed_by: str
    signed_at: str
    report_sha256: str
    note: str = ""

    def to_dict(self) -> dict:
        return dict(
            run_id=self.run_id, borrower=self.borrower, signed_by=self.signed_by,
            signed_at=self.signed_at, report_sha256=self.report_sha256, note=self.note,
        )


def _report_path(out_dir: Path, borrower: str, run_id: str) -> Path:
    return Path(out_dir) / borrower / f"run_{run_id}.json"


def _signoff_path(out_dir: Path, borrower: str, run_id: str) -> Path:
    return Path(out_dir) / borrower / f"run_{run_id}.signoff.json"


def _report_hash(report_path: Path) -> str:
    return hashlib.sha256(report_path.read_bytes()).hexdigest()


def get_signoff(out_dir: str | Path, borrower: str, run_id: str) -> dict | None:
    """Return the existing sign-off record for this run, or None."""
    path = _signoff_path(Path(out_dir), borrower, run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def sign_off_run(
    out_dir: str | Path,
    log_path: str | Path,
    borrower: str,
    run_id: str,
    signed_by: str,
    note: str = "",
) -> SignOffRecord:
    """Record a human sign-off for one run's working paper.

    Raises ReportNotFoundError if the run's report doesn't exist yet, and
    AlreadySignedOffError if it already has a signer of record.
    """
    if not signed_by or not signed_by.strip():
        raise ValueError("signed_by is required (who is approving this run)")

    out = Path(out_dir)
    report_path = _report_path(out, borrower, run_id)
    if not report_path.exists():
        raise ReportNotFoundError(
            f"no report found for {borrower} run {run_id} at {report_path}"
        )

    existing = get_signoff(out, borrower, run_id)
    if existing:
        raise AlreadySignedOffError(
            f"{borrower} run {run_id} was already signed off by "
            f"{existing['signed_by']} at {existing['signed_at']}"
        )

    record = SignOffRecord(
        run_id=run_id,
        borrower=borrower,
        signed_by=signed_by.strip(),
        signed_at=_now(),
        report_sha256=_report_hash(report_path),
        note=note.strip(),
    )

    _signoff_path(out, borrower, run_id).write_text(
        json.dumps(record.to_dict(), indent=2), encoding="utf-8"
    )

    RunLog(log_path).append({"event": "run_signed_off", **record.to_dict()})
    return record


def verify_signoff(out_dir: str | Path, borrower: str, run_id: str) -> bool:
    """True only if a sign-off exists AND its recorded report hash still
    matches the current report file -- i.e. the report was not changed since
    it was signed off."""
    out = Path(out_dir)
    record = get_signoff(out, borrower, run_id)
    if not record:
        return False
    report_path = _report_path(out, borrower, run_id)
    if not report_path.exists():
        return False
    return _report_hash(report_path) == record["report_sha256"]
