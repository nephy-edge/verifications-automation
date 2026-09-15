"""Tests for the SOP 1 change-detection watcher (cash_watcher.py).

Covers the pure logic: watermark stability, state persistence, change
detection given a fetch, the cut-off guard (including borrower-variant date
columns), retry-on-failed-run, and the end-to-end auto-run. Networked fetching
is monkeypatched so the tests run offline and deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phase0_foundations.config import WatcherConfig, load_config
from cash_watcher import (
    FULL_TAPE_LIMIT,
    _bank_row_dedup_key,
    _cut_off_status,
    _dedupe_bank_rows,
    _effective_tape_limit,
    _hash_rows,
    _latest_date,
    _load_state,
    _headless_run,
    detect_changes,
)


@pytest.fixture
def cfg(tmp_path):
    """:returns: a Config with the watcher pointing at a temp state file."""
    from phase0_foundations.config import Config

    base = Config()
    base.services.api_url = "http://test/api"
    base.services.api_key_env = "WATCHER_TEST_API_KEY"
    base.sop1 = WatcherConfig(
        enabled=True,
        interval_seconds=10,
        state_file=str(tmp_path / "state.json"),
        tape_fetch_limit=1000,
        run_on_change=True,
        borrowers=["leasy"],
    )
    return base


def test_hash_rows_is_stable_and_order_insensitive():
    a = [{"loan_id": "1", "amount": "10"}, {"loan_id": "2", "amount": "20"}]
    b = [{"amount": "20", "loan_id": "2"}, {"loan_id": "1", "amount": "10"}]
    assert _hash_rows(a) == _hash_rows(b)
    c = [{"loan_id": "1", "amount": "10"}, {"loan_id": "2", "amount": "21"}]
    assert _hash_rows(a) != _hash_rows(c)


def test_hash_rows_catches_row_add_remove():
    a = [{"loan_id": "1"}]
    b = [{"loan_id": "1"}, {"loan_id": "2"}]
    assert _hash_rows(a) != _hash_rows(b)


def test_latest_date_uses_given_column():
    rows = [{"begin_date": "2026-01-01"}, {"begin_date": "2026-06-15"}, {"begin_date": ""}]
    assert _latest_date(rows, "begin_date") == "2026-06-15"
    assert _latest_date([{"begin_date": ""}], "begin_date") is None
    # borrower-variant columns are respected (payjoy -> origination_date)
    rows2 = [{"origination_date": "2026-03-01"}, {"origination_date": "2026-04-20"}]
    assert _latest_date(rows2, "origination_date") == "2026-04-20"


def test_state_round_trip(tmp_path):
    path = tmp_path / "s.json"
    state = {"borrowers": {"leasy": {"watermark": "abc", "rows": 3}}}
    from cash_watcher import _save_state

    _save_state(path, state)
    assert _load_state(path) == state
    # missing file -> empty shell
    assert _load_state(tmp_path / "nope.json") == {"borrowers": {}}
    # corrupt file -> empty shell
    path.write_text("{not json", encoding="utf-8")
    assert _load_state(path) == {"borrowers": {}}


def test_detect_changes_reports_new_and_unchanged(cfg, monkeypatch):
    tape_a = [{"loan_id": "1", "begin_date": "2026-01-01"}]
    tape_b = [{"loan_id": "1", "begin_date": "2026-01-01"}, {"loan_id": "2", "begin_date": "2026-01-02"}]

    def fake_fetch(api_url, api_key, borrower, limit):
        assert api_url.startswith("http://test")
        assert api_key == ""
        return list(tape_a)  # first poll returns tape A

    monkeypatch.setattr("cash_watcher._fetch_tape", fake_fetch)
    cfg.services.api_key_env = "REDSHIFT_API_KEY"  # env unset -> "" is fine here

    changed, state, rows = detect_changes(cfg)
    assert changed == ["leasy"]
    assert state["borrowers"]["leasy"]["rows"] == 1
    assert rows["leasy"] == tape_a
    # second poll with same tape -> no change
    changed2, state2, _ = detect_changes(cfg)
    assert changed2 == []
    # third poll with updated tape -> change
    monkeypatch.setattr("cash_watcher._fetch_tape", lambda *a, **k: list(tape_b))
    changed3, _, _ = detect_changes(cfg)
    assert changed3 == ["leasy"]


def test_detect_changes_stores_borrower_date_column(cfg, monkeypatch):
    # leasy is default-mapped so begin_date is used; the state records it.
    tape = [{"loan_id": "1", "begin_date": "2026-01-01"}]
    monkeypatch.setattr("cash_watcher._fetch_tape", lambda *a, **k: list(tape))
    _, state, _ = detect_changes(cfg)
    assert state["borrowers"]["leasy"]["date_column"] == "begin_date"
    assert state["borrowers"]["leasy"]["latest_date"] == "2026-01-01"


def test_detect_changes_handles_fetch_error(cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr("cash_watcher._fetch_tape", boom)
    changed, state, rows = detect_changes(cfg)
    assert changed == []
    assert state["borrowers"] == {}
    assert rows == {}


def test_detect_changes_list_fetch_error_is_not_fatal(cfg, monkeypatch):
    # No explicit borrowers -> the watcher asks /borrowers. If that fails
    # (e.g. transient outage / unset key), it must degrade to "no changes"
    # rather than crash a long-running loop.
    cfg.sop1.borrowers = []

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr("cash_watcher._fetch_borrowers", boom)
    changed, state, rows = detect_changes(cfg)
    assert changed == []
    assert state["borrowers"] == {}
    assert rows == {}


def test_cut_off_status_uses_latest_date(cfg):
    cfg.sop1.cut_off_date = "2026-06-30"
    cfg.sop1.cut_off_mode = "backdate"
    bstate = {"latest_date": "2026-07-01", "date_column": "begin_date"}
    assert _cut_off_status(cfg, bstate) == "post_cut_off"
    bstate2 = {"latest_date": "2026-06-30", "date_column": "begin_date"}
    assert _cut_off_status(cfg, bstate2) == "ok"
    # no cut-off configured -> always ok
    cfg.sop1.cut_off_date = ""
    assert _cut_off_status(cfg, {"latest_date": "2030-01-01"}) == "ok"


def test_cut_off_guard_respects_borrower_variant_date_column(cfg, monkeypatch):
    # A borrower (payjoy) whose tape uses origination_date (not begin_date)
    # must still be flagged post-cut-off via its real date column. Mirrors the
    # real config.yaml `loan_tape_columns.overrides.payjoy.begin_date`.
    cfg.sop1.borrowers = ["payjoy"]
    cfg.sop1.cut_off_date = "2026-06-30"
    cfg.loan_tape_columns.overrides["payjoy"] = {"begin_date": "origination_date"}
    tape = [{"loan_id": "1", "origination_date": "2026-07-15", "begin_date": ""}]
    monkeypatch.setattr("cash_watcher._fetch_tape", lambda *a, **k: list(tape))
    changed, state, _ = detect_changes(cfg)
    assert changed == ["payjoy"]
    assert state["borrowers"]["payjoy"]["latest_date"] == "2026-07-15"
    assert _cut_off_status(cfg, state["borrowers"]["payjoy"]) == "post_cut_off"


def test_watch_config_from_yaml_default():
    # config.yaml's sop1.watcher block must parse and default sensibly.
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert cfg.sop1.interval_seconds == 600
    assert cfg.sop1.cut_off_mode in ("hard_stop", "backdate")
    # SOP-1's own discrepancy tolerance (0.01%), distinct from the shared
    # cash_balance_variance (1%) used everywhere else.
    assert cfg.sop1.cash_discrepancy_pct == 0.0001
    assert cfg.thresholds.cash_balance_variance == 0.01


def test_effective_tape_limit_zero_means_full_tape(cfg):
    # 0 (the default / config.yaml value) means "fetch the full tape".
    cfg.sop1.tape_fetch_limit = 0
    assert _effective_tape_limit(cfg) == FULL_TAPE_LIMIT
    # a negative value also means "full tape" (fall back to the sentinel).
    cfg.sop1.tape_fetch_limit = -1
    assert _effective_tape_limit(cfg) == FULL_TAPE_LIMIT
    # a positive value is passed through as a sampled window.
    cfg.sop1.tape_fetch_limit = 1000
    assert _effective_tape_limit(cfg) == 1000
    assert _effective_tape_limit(cfg) != FULL_TAPE_LIMIT


def test_headless_run_produces_working_paper(tmp_path):
    """End-to-end (network-free) proof that a detected tape change yields a
    completed VerificationRun and a report file — the full auto-run path minus
    the fetch. Uses a synthetic redshift-style per-loan tape row that
    normalize_loan_tape_row understands."""
    from phase0_foundations.config import Config

    cfg = Config()
    cfg.log_path = tmp_path / "verifications.log.jsonl"
    cfg.out_dir = tmp_path / "out"

    # Two loans with known money fields; normalize_loan_tape_row derives a
    # disbursement (principal at begin_date) and a collection (paid-down) per row.
    tape = [
        {
            "loan_id": "L1",
            "begin_date": "2026-01-05",
            "principal_amount": "1000.00",
            "total_loan_amount": "1000.00",
            "principal_outstanding": "500.00",
            "interest_outstanding": "0",
            "fee_outstanding": "0",
            "penalty_outstanding": "0",
            "closure_date": "",
            "company_due_date": "2026-02-01",
            "currency": "KES",
            "status": "active",
            "country": "KE",
            "days_past_due": "0",
        },
        {
            "loan_id": "L2",
            "begin_date": "2026-01-20",
            "principal_amount": "2000.00",
            "total_loan_amount": "2000.00",
            "principal_outstanding": "2000.00",
            "interest_outstanding": "0",
            "fee_outstanding": "0",
            "penalty_outstanding": "0",
            "closure_date": "",
            "company_due_date": "2026-02-15",
            "currency": "KES",
            "status": "active",
            "country": "KE",
            "days_past_due": "0",
        },
    ]

    run = _headless_run(cfg, "leasy", tape, reported={})

    assert run.status == "done"
    assert run.aggregates["collections"] > 0
    assert run.aggregates["disbursements"] > 0
    # the watcher has no independent bank/mobile side, so the report must say so
    assert run.inputs["independent_source_present"] is False
    assert "NOT VERIFIED" in run.inputs["reconciliation_scope"]
    # a report JSON was written under out/<borrower>
    report_json = tmp_path / "out" / "leasy" / f"run_{run.id}.json"
    assert report_json.exists()
    # an audit-log line was appended for the run
    log_text = (tmp_path / "verifications.log.jsonl").read_text(encoding="utf-8")
    assert "watcher_run_completed" in log_text


def test_failed_run_reverts_watermark_for_retry(cfg, monkeypatch, tmp_path):
    """If _headless_run raises (run status 'failed'), the stored watermark must
    be reverted so the next poll re-detects and retries instead of silently
    consuming the change."""
    from cash_watcher import _poll_once, _load_state

    tape = [{"loan_id": "1", "begin_date": "2026-01-01"}]
    monkeypatch.setattr("cash_watcher._fetch_tape", lambda *a, **k: list(tape))
    monkeypatch.setattr("cash_watcher._fetch_borrowers", lambda *a, **k: ["leasy"])
    calls = {"n": 0}

    def fake_headless_run(cfg, borrower, rows, reported, bank_rows=None):
        calls["n"] += 1
        from phase0_foundations.models import VerificationRun

        run = VerificationRun(id="x", status="failed")
        return run

    monkeypatch.setattr("cash_watcher._headless_run", fake_headless_run)

    _poll_once(cfg, dry_run=False)
    state = _load_state(Path(cfg.sop1.state_file))
    # watermark reverted -> a later detect_changes sees it as (still) changed
    assert "watermark" not in state["borrowers"]["leasy"]
    assert state["borrowers"]["leasy"]["run_status"] == "failed"

    # next poll retries (headless_run called again)
    _poll_once(cfg, dry_run=False)
    assert calls["n"] == 2


def test_headless_run_with_bank_rows_marks_independent_verified(tmp_path):
    """Feeding the independent bank/mobile side must flip
    `independent_source_present` to True and reconcile reported (tape) vs
    calculated (bank) rather than tape-vs-tape."""
    from phase0_foundations.config import Config

    cfg = Config()
    cfg.log_path = tmp_path / "verifications.log.jsonl"
    cfg.out_dir = tmp_path / "out"

    tape = [
        {
            "loan_id": "L1", "begin_date": "2026-01-05",
            "principal_amount": "1000.00", "total_loan_amount": "1000.00",
            "principal_outstanding": "0", "interest_outstanding": "0",
            "fee_outstanding": "0", "penalty_outstanding": "0",
            "closure_date": "", "company_due_date": "2026-01-20",
            "currency": "KES", "status": "active", "country": "KE", "days_past_due": "0",
        },
    ]
    # independent bank rows: same KES amounts the tape reports as collected/paid
    bank = [
        {"sheet": "bank", "source_type": "bank", "value_date": "2026-01-20",
         "amount": 1000.0, "direction": "in", "currency": "KES",
         "description": "borrower repayment", "account_ref": "acc1", "confidence": 1.0},
    ]

    run = _headless_run(cfg, "leasy", tape, reported={}, bank_rows=bank)

    assert run.status == "done"
    assert run.inputs["independent_source_present"] is True
    assert "verified against independent" in run.inputs["reconciliation_scope"]
    # the calculated (independent) side is populated from the bank rows
    assert run.aggregates["calculated_collections"] == run.aggregates["collections"]
    # a real (non-vacuous) reconciliation ran — bank+tape rows both feed anomaly detection
    assert run.inputs["bank_statement_count"] == 1


def test_headless_run_uses_sop1_discrepancy_pct_not_the_shared_threshold(tmp_path):
    """SOP-1 Step 4 says 'Flag discrepancies > 0.01%' -- 100x tighter than
    the shared cash_balance_variance (1%) every other reconciliation in the
    app uses. A 0.5% cash variance sits between the two: it must NOT trip the
    shared 1% threshold but MUST trip cash_watcher's own 0.01% one."""
    from phase0_foundations.config import Config

    cfg = Config()
    cfg.log_path = tmp_path / "verifications.log.jsonl"
    cfg.out_dir = tmp_path / "out"
    assert cfg.thresholds.cash_balance_variance == 0.01   # shared 1%, unchanged
    assert cfg.sop1.cash_discrepancy_pct == 0.0001         # SOP-1's own 0.01%

    tape = [
        {
            "loan_id": "L1", "begin_date": "2026-01-05",
            "principal_amount": "1000.00", "total_loan_amount": "1000.00",
            "principal_outstanding": "0", "interest_outstanding": "0",
            "fee_outstanding": "0", "penalty_outstanding": "0",
            "closure_date": "", "company_due_date": "2026-01-20",
            "currency": "USD", "status": "active", "country": "KE", "days_past_due": "0",
        },
    ]
    # Independent side is 0.5% below the reported cash_total (1,000,000 vs
    # 995,000) -- within the shared 1% tolerance, but not SOP-1's 0.01%.
    bank = [
        {"sheet": "bank", "source_type": "bank", "value_date": "2026-01-20",
         "amount": 995_000.0, "direction": "in", "currency": "USD",
         "description": "repayment", "account_ref": "acc1", "confidence": 1.0},
    ]
    reported = {"collections": 0.0, "disbursements": 0.0, "cash_total": 1_000_000.0}

    run = _headless_run(cfg, "leasy", tape, reported=reported, bank_rows=bank)

    assert run.status == "done"
    cash_exceptions = [e for e in run.exceptions if "cash" in e.id]
    assert cash_exceptions, "0.5% cash variance must be flagged under SOP-1's 0.01% tolerance"


def test_fetch_bank_statement_rows_ingests_and_is_idempotent(cfg, monkeypatch, tmp_path):
    """New statement files are downloaded + retained once; a second call re-parses
    the retained file locally without re-downloading. Uses a fake drive_inbox."""
    import cash_watcher as cw
    from phase0_foundations.config import BankStatementsConfig

    cfg.sop1.bank_statements = BankStatementsConfig(enabled=True, inbox_folder="Bank Statements")
    cfg.out_dir = tmp_path / "out"
    downloads = {"n": 0}

    class FakeFile:
        fingerprint = "abc123@2026-01-01T00:00:00"
        file_id = "abc123"
        name = "stmt.csv"
        mime = "text/csv"
        modified_at = "2026-01-01T00:00:00"

    import phase0_foundations.drive_inbox as di

    monkeypatch.setattr(di, "list_new_statements", lambda folder: [FakeFile()])

    def fake_download(file_id, name):
        downloads["n"] += 1
        return b"date,amount,type,currency\n2026-01-05,1000,collections,KES\n"

    monkeypatch.setattr(di, "download_file", fake_download)
    monkeypatch.setattr("cash_watcher._save_state", lambda p, s: None)

    rows, retained_new = cw._fetch_bank_statement_rows(cfg, "leasy", {"borrowers": {"leasy": {}}})
    assert retained_new == 1
    assert downloads["n"] == 1
    # the raw file was retained as the audit trail
    retained_dir = tmp_path / "out" / "leasy" / "statements"
    assert any(retained_dir.iterdir())

    # Already-ingested fingerprint -> no re-download.
    state2 = {"borrowers": {"leasy": {"ingested_statements": [FakeFile.fingerprint]}}}
    _, retained_new2 = cw._fetch_bank_statement_rows(cfg, "leasy", state2)
    assert retained_new2 == 0
    assert downloads["n"] == 1  # no second download


def test_bank_statements_disabled_returns_empty(cfg):
    from cash_watcher import _fetch_bank_statement_rows

    cfg.sop1.bank_statements.enabled = False
    rows, retained = _fetch_bank_statement_rows(cfg, "leasy", {"borrowers": {}})
    assert rows == []
    assert retained == 0


def test_bank_row_dedup_key_normalises_for_ocr_noise():
    a = {"value_date": "2026-06-02", "amount": "3019.09", "direction": "in",
         "description": "TxN-20260602 Direct Deposit"}
    b = {"value_date": "2026-06-02", "amount": 3019.09, "direction": "IN",
         "description": "TXN 20260602 direct  deposit"}
    # same transaction despite OCR case/spacing differences
    assert _bank_row_dedup_key(a) == _bank_row_dedup_key(b)
    # a genuinely different amount/date is a different identity
    c = {"value_date": "2026-06-03", "amount": 3019.09, "direction": "in",
         "description": "TxN-20260602 Direct Deposit"}
    assert _bank_row_dedup_key(a) != _bank_row_dedup_key(c)


def test_dedupe_bank_rows_keeps_first_occurrence():
    rows = [
        {"key": "bank:f1:0", "value_date": "2026-06-02", "amount": 3019.09,
         "direction": "in", "description": "Payroll"},
        {"key": "bank:f2:0", "value_date": "2026-06-02", "amount": 3019.09,
         "direction": "in", "description": "Payroll"},
        {"key": "bank:f2:1", "value_date": "2026-06-03", "amount": 21.05,
         "direction": "out", "description": "Shell"},
    ]
    deduped = _dedupe_bank_rows(rows)
    assert len(deduped) == 2
    assert deduped[0]["key"] == "bank:f1:0"  # first occurrence (and its key) kept
    assert deduped[1]["key"] == "bank:f2:1"


def test_detect_changes_preserves_ingested_statements(cfg, monkeypatch):
    """A change-triggered poll rebuilds the borrower entry but must keep
    `ingested_statements` (the Drive drop-folder idempotency record), so
    already-downloaded statement files are not re-downloaded on every change."""
    from pathlib import Path

    from cash_watcher import _save_state

    tape = [{"loan_id": "1", "begin_date": "2026-01-01"}]
    monkeypatch.setattr("cash_watcher._fetch_tape", lambda *a, **k: list(tape))
    state = {"borrowers": {"leasy": {"ingested_statements": ["fp1@2026-01-01T00:00:00"]}}}
    _save_state(Path(cfg.sop1.state_file), state)

    changed, state, _ = detect_changes(cfg)
    assert changed == ["leasy"]
    assert state["borrowers"]["leasy"]["ingested_statements"] == ["fp1@2026-01-01T00:00:00"]


def test_api_get_surfaces_server_error_detail(monkeypatch):
    """A non-2xx response must be re-raised with the API's own `detail` (e.g.
    "Query failed: connection timed out" on a DB outage, or "Unknown borrower
    ...") in the message, so the watcher's log line is diagnosable instead of a
    bare 'HTTP Error 500'."""
    import io
    import urllib.error

    from cash_watcher import _api_get

    body = io.BytesIO(b'{"detail": "Query failed: connection timed out"}')
    http_err = urllib.error.HTTPError(
        "http://test/api/loan-tape?borrower=leasy", 500,
        "Internal Server Error", {}, body,
    )

    def fake_urlopen(req, timeout=130):
        raise http_err

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Query failed: connection timed out"):
        _api_get("http://test/api", "key", "/loan-tape?borrower=leasy&limit=1000000")


def test_api_get_surfaces_non_json_error_body(monkeypatch):
    """A non-JSON error body still surfaces instead of a bare status code."""
    import io
    import urllib.error

    from cash_watcher import _api_get

    body = io.BytesIO(b"upstream gateway timeout")
    http_err = urllib.error.HTTPError(
        "http://test/api/borrowers", 502, "Bad Gateway", {}, body,
    )

    def fake_urlopen(req, timeout=130):
        raise http_err

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="upstream gateway timeout"):
        _api_get("http://test/api", "key", "/borrowers")


def test_send_run_email_sends_when_enabled(cfg, monkeypatch, tmp_path):
    """After a done run the watcher emails the working paper (.docx + .json);
    disabled config skips the send entirely."""
    from cash_watcher import _send_run_email
    from phase0_foundations.config import EmailConfig
    from phase0_foundations.models import VerificationRun

    cfg.out_dir = tmp_path / "out"
    run = VerificationRun(id="abc123", status="done")
    (cfg.out_dir / "leasy").mkdir(parents=True, exist_ok=True)
    (cfg.out_dir / "leasy" / "run_abc123.json").write_text("{}", encoding="utf-8")

    calls = {}

    def fake_send(ec, paths, subject, body_html=None):
        calls["subject"] = subject
        calls["paths"] = [str(p) for p in paths]
        calls["body_html"] = body_html
        return True

    import phase0_foundations.emailer as emailer

    monkeypatch.setattr(emailer, "send_report_email", fake_send)

    # disabled -> no call
    _send_run_email(cfg, "leasy", run)
    assert calls == {}

    # enabled -> called with the .docx (built) + .json paths and an HTML body
    cfg.sop1.email = EmailConfig(enabled=True, from_addr="me@gmail.com",
                                 to_addrs=["x@y.com"])
    _send_run_email(cfg, "leasy", run)
    assert "leasy" in calls["subject"]
    assert calls["paths"] == [
        str(cfg.out_dir / "leasy" / "run_abc123.docx"),
        str(cfg.out_dir / "leasy" / "run_abc123.json"),
    ]
    assert "<html" in (calls.get("body_html") or "")
    # the .docx working paper was actually built on disk
    assert (cfg.out_dir / "leasy" / "run_abc123.docx").exists()


def test_send_run_email_logs_failure_without_raising(cfg, monkeypatch, tmp_path, capsys):
    """A mail outage must be logged, never fatal to the poll."""
    from cash_watcher import _send_run_email
    from phase0_foundations.config import EmailConfig
    from phase0_foundations.models import VerificationRun

    cfg.out_dir = tmp_path / "out"
    run = VerificationRun(id="abc123", status="done")
    cfg.sop1.email = EmailConfig(enabled=True, from_addr="me@gmail.com", to_addrs=["x@y.com"])

    import phase0_foundations.emailer as emailer

    def boom(ec, paths, subject):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(emailer, "send_report_email", boom)
    _send_run_email(cfg, "leasy", run)  # must not raise
    assert "send failed" in capsys.readouterr().err


def test_detect_new_statements_flags_uningested_only(monkeypatch):
    """A borrower is flagged when its inbox has a file whose fingerprint is not
    yet recorded as ingested; already-ingested files and borrowers with no
    matching folder are not flagged. Uses the configured per-borrower folder."""
    from cash_watcher import _detect_new_statements
    from phase0_foundations.config import BankStatementsConfig, Config

    cfg = Config()
    cfg.sop1.borrowers = ["leasy", "other"]
    cfg.sop1.bank_statements = BankStatementsConfig(
        enabled=True, inbox_folder="ignored",
        folder_by_borrower={"leasy": "L", "other": "O"},
    )
    state = {"borrowers": {"leasy": {"ingested_statements": ["old@2026-01-01"]}}}

    class NewFile:
        fingerprint = "new@2026-06-01"

    class OldFile:
        fingerprint = "old@2026-01-01"

    import phase0_foundations.drive_inbox as di

    monkeypatch.setattr(di, "list_new_statements", lambda folder: (
        [NewFile(), OldFile()] if folder == "L" else []
    ))
    assert _detect_new_statements(cfg, state, ["leasy", "other"]) == {"leasy"}


def test_detect_new_statements_tolerates_inbox_error(monkeypatch, capsys):
    """A failing Drive listing must not fail the poll — it just contributes no
    statement-triggered runs."""
    from cash_watcher import _detect_new_statements
    from phase0_foundations.config import BankStatementsConfig, Config

    cfg = Config()
    cfg.sop1.borrowers = ["leasy"]
    cfg.sop1.bank_statements = BankStatementsConfig(
        enabled=True, inbox_folder="L",
    )
    import phase0_foundations.drive_inbox as di

    def boom(folder):
        raise RuntimeError("token expired")

    monkeypatch.setattr(di, "list_new_statements", boom)
    assert _detect_new_statements(cfg, {"borrowers": {}}, ["leasy"]) == set()
    assert "statement inbox check failed" in capsys.readouterr().err


def test_detect_changes_triggers_on_new_statement_even_when_tape_unchanged(cfg, monkeypatch):
    """The tape watermark is pre-seeded as unchanged; the only change is a new
    statement in the inbox — and that alone must put the borrower in `changed`."""
    import phase0_foundations.drive_inbox as di
    from cash_watcher import _hash_rows, _save_state
    from pathlib import Path
    from phase0_foundations.config import BankStatementsConfig

    cfg.sop1.bank_statements = BankStatementsConfig(enabled=True, inbox_folder="L")
    tape = [{"loan_id": "1", "begin_date": "2026-01-01"}]
    monkeypatch.setattr("cash_watcher._fetch_tape", lambda *a, **k: list(tape))
    # pre-seed: leasy already knows this exact tape (unchanged watermark)
    _save_state(Path(cfg.sop1.state_file), {
        "borrowers": {"leasy": {"watermark": _hash_rows(tape), "rows": 1,
                                "latest_date": "2026-01-01", "date_column": "begin_date"}}
    })

    class NewFile:
        fingerprint = "new@2026-06-01"

    monkeypatch.setattr(di, "list_new_statements", lambda folder: [NewFile()])

    changed, state, rows = detect_changes(cfg)
    assert changed == ["leasy"]
    assert rows["leasy"] == tape


def test_fetch_bank_statement_rows_dedupes_across_files(cfg, monkeypatch, tmp_path):
    """When the same transaction is parsed via the retained re-parse AND a
    newly-downloaded file in the same cycle, it must be counted once."""
    import cash_watcher as cw
    from phase0_foundations.config import BankStatementsConfig

    cfg.sop1.bank_statements = BankStatementsConfig(enabled=True, inbox_folder="Bank Statements")
    cfg.out_dir = tmp_path / "out"
    retained_dir = tmp_path / "out" / "leasy" / "statements"
    retained_dir.mkdir(parents=True, exist_ok=True)

    # a retained CSV file that will be re-parsed (step 1)...
    retained = retained_dir / "stmt1.csv"
    retained.write_text("date,amount,type,currency\n2026-01-05,1000,collections,KES\n",
                        encoding="utf-8")

    # ...and the SAME transaction in a new inbox file (step 2) -> one dup.
    class FakeFile:
        fingerprint = "xyz999@2026-01-01T00:00:00"
        file_id = "xyz999"
        name = "stmt2.csv"
        mime = "text/csv"
        modified_at = "2026-01-01T00:00:00"

    import phase0_foundations.drive_inbox as di

    monkeypatch.setattr(di, "list_new_statements", lambda folder: [FakeFile()])
    monkeypatch.setattr(di, "download_file", lambda file_id, name: retained.read_bytes())
    monkeypatch.setattr("cash_watcher._save_state", lambda p, s: None)

    rows, retained_new = cw._fetch_bank_statement_rows(cfg, "leasy", {"borrowers": {}})
    assert retained_new == 1
    assert len(rows) == 1  # de-duplicated to a single row across the two files

