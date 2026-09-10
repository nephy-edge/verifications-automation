"""Tests for the SOP 1 Drive drop-folder reader (phase0_foundations/drive_inbox.py).

Only the pure, network-free surface is unit-tested: the InboxFile fingerprint
(used for idempotent ingestion) and the config parsing of
`config.yaml`'s `sop1.watcher.bank_statements`. The Drive API calls themselves
(list/download) are integration-glue that requires OAuth + network and are
deliberately not exercised here (consistent with this project's convention of
not unit-testing live-external glue).
"""

from __future__ import annotations

from pathlib import Path

from phase0_foundations.config import load_config
from phase0_foundations.drive_inbox import InboxFile


def test_inbox_file_fingerprint_is_fileid_plus_modified():
    f = InboxFile(file_id="abc", name="s.pdf", mime="application/pdf", modified_at="2026-01-01T00:00:00")
    assert f.fingerprint == "abc@2026-01-01T00:00:00"
    # an edited/re-uploaded file has a new modifiedTime -> treated as new
    f2 = InboxFile(file_id="abc", name="s.pdf", mime="application/pdf", modified_at="2026-02-01T00:00:00")
    assert f2.fingerprint != f.fingerprint
    # a distinct file id is distinct even with the same modified time
    f3 = InboxFile(file_id="zzz", name="s.pdf", mime="application/pdf", modified_at="2026-01-01T00:00:00")
    assert f3.fingerprint != f.fingerprint


def test_bank_statements_config_reads_enabled_folder():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    bs = cfg.sop1.bank_statements
    assert bs.enabled is True
    assert bs.inbox_folder == "1_ReH5RpdCr0vXfuKMj7KzjOlFjwOo31F"


def test_bank_statements_disabled_by_default_in_dataclass():
    # A bare WatcherConfig/BankStatementsConfig defaults to disabled (safety),
    # so the enabled:true in config.yaml is an explicit opt-in, not the default.
    from phase0_foundations.config import BankStatementsConfig, WatcherConfig

    assert WatcherConfig().bank_statements.enabled is False
    assert BankStatementsConfig().enabled is False


def test_bank_statements_folder_for_prefers_borrower_override():
    from phase0_foundations.config import BankStatementsConfig

    bs = BankStatementsConfig(
        enabled=True,
        inbox_folder="Bank Statements",
        folder_by_borrower={"leasy": "Leasy Statements"},
    )
    assert bs.folder_for("leasy") == "Leasy Statements"
    assert bs.folder_for("other") == "Bank Statements"
