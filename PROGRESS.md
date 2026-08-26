# PROGRESS.md — Verifications Automation

Session/task log (per workspace AGENTS.md rule 6). Updated on every meaningful
action. Source of intent: `Overview_-Verifications.html` (Lendable Risk workflow
record).

## Goal
Build the "Verifications Automation" workflow in a standalone folder, one
subfolder per phase: ingest multi-format data (loan tape, bank statements,
mobile money, ledger, cash map), parse unstructured PDFs, compute independent
aggregates, reconcile against reported figures with encoded thresholds, detect
anomalies, generate working papers/exception reports, and drive a human
review/sign-off loop with a feedback/hardening feedback loop.

## Scope decisions
- New standalone project: `verifications-automation/`, one subfolder per phase.
- `Bank Access/` is untouched (its aggregator-API ingestion is out of scope here;
  this workflow ingests submitted multi-format files per the brief's Phase 1).
- Deterministic-first: code does all arithmetic; LLM only for genuine
  interpretation; human for sign-off (workflow record A4/B2).
- All deps install only into this project's `.venv` (workspace AGENTS.md rule 7).

## Completed
- Created `verifications-automation/` with one subfolder per phase
  (`phase0_foundations` … `phase5_feedback_hardening`; underscore names so each
  is a valid, directly importable Python package).
- Root scaffolding: README.md, .gitignore, .env.example, requirements.txt,
  config.yaml.
- Built all five phases:
  - Phase 0: `config.py` (YAML->typed Thresholds/LLM/Config), `log.py`
    (append-only jsonl), `models.py` (VerificationRun/ExceptionItem/ReviewAction).
  - Phase 1: `ingest.py` (multi-format normalizer: tape/bank/mobile/ledger/cash
    map -> canonical rows), `extract.py` (pdfplumber rules + confidence), the
    methodology (A5), and `prompts/extract_prompt.txt`.
  - Phase 2: `calculate.py` (independent aggregates, deterministic) and
    `reconcile.py` (B3 thresholds -> ExceptionItems) + tests.
  - Phase 3: `anomaly.py` (dup/round/frequency rules), `rank.py` (0.75 forensic
    route), `reporter.py` (JSON+Markdown working papers), `prompts/anomaly_prompt.txt`.
  - Phase 4: `approval.py` (pending -> reviewed -> approved/rejected + sign-off,
    logged).
  - Phase 5: `harden.py` (feedback + rule promotion, logged).
- `runner.py`: end-to-end CLI tying the phases together.
- Created `.venv` (inside the project) and installed requirements into it only.
  All runs use the venv interpreter (workspace rule 7).
- Verified (workspace rule 4) with the venv interpreter:
  - all modules `py_compile` clean;
  - `tests/test_calculate.py` and `tests/test_reconcile.py` pass;
  - all six phase packages import OK;
  - end-to-end `runner.py` smoke test on `samples/` (loan tape CSV + real Absa
    PDF) exits 0, computes aggregates, flags 7 exceptions (cash-balance variance
    2798% -> severity 1.0; collections variance 15.9% > 2%; duplicate + round-
    dollar anomalies), writes JSON+Markdown report, and appends to
    `verifications.log.jsonl`.

## Fixes during build
- `phase4_human_review/__init__.py` imported nonexistent `approve` -> corrected to
  `approved`.
- `phase1_ingestion_parsing/ingest.py`: missing `enumerate()` around
  `df.to_dict("records")` caused "too many values to unpack" -> fixed.
- `phase3_anomaly_reporting/reporter.py`: non-ASCII em-dash in Markdown header
  replaced with ASCII hyphen for clean cross-encoding output.
- `phase1_ingestion_parsing/ingest.py`: the generic tape schema
  (`date`/`amount`/`type`/`currency`) silently dropped every row of a real
  production loan-tape export (per-loan snapshot: `loan_id`, `begin_date`,
  `principal_amount`, `total_loan_amount`, `principal_outstanding`, etc.) —
  ran to completion with `transaction_count: 0` and no error. Added
  `normalize_loan_tape_row()`: disbursement = `principal_amount` at
  `begin_date`; collections = `total_loan_amount` minus everything still
  outstanding (principal/interest/fees/penalties), dated at `closure_date`
  (or `company_due_date` if still open). Detected automatically when those
  columns are present; the simplified sample schema still works unchanged.
  Verified against a real 100-loan tape: 198 events, collections
  283,865.93 / disbursements 261,610.18. Added `tests/test_ingest.py`
  (paid / defaulted / active-partial cases).
- `app/streamlit_app.py`: a tape-only run (no bank/mobile/ledger) reconciled
  the tape's own reported figures against a `calculated` side of literal
  zero, flagging "inf% variance" as if it were a real finding. Reconciliation
  now only runs per-metric when both sides have underlying data
  (`has_tape`/`has_ledger`/`has_independent` gates in `_run_pipeline`); the
  Results tab shows "no data uploaded" instead of a misleading 0.00 and
  surfaces which comparisons were skipped and why.
- `phase3_anomaly_reporting/anomaly.py`: the round-dollar rule flagged any
  whole-dollar amount >= $100 — on a real 100-loan salary-advance tape that's
  ~half the portfolio (round loan sizes are the product norm, not a
  falsification signal), burying the one real finding
  (`L958P9N2`, 18x-median disbursement) under noise. Redefined "round" as
  round-to-100/round-to-1000 (not merely "no cents") and gated the rule on
  materiality (`round_dollar_materiality_multiple`, default 3x median) so it
  only fires on amounts that are both round and unusually large. Also fixed
  the duplicate-transaction rule to key on (description, amount, date)
  instead of description alone (a recurring bank narration category is
  normal; the same transaction repeating is not), and added the still-missing
  A5 red flag for same-day disbursement+collection round-tripping. Real-tape
  exception count dropped 104 -> 7, with the real finding at severity 1.00.
  Added `tests/test_anomaly.py` (7 cases). New threshold in
  `config.yaml`/`config.py`: `round_dollar_materiality_multiple`.
- `phase1_ingestion_parsing/extract.py`: the regex parser assumed a single
  DD-MM-YYYY date column; a real BBVA Peru statement uses two DD-MM columns
  (no year) plus trailing amount/tax/balance figures, so only 1 of 32 real
  transaction lines parsed (and that one only by luck — "LEASY II 03.06.26"
  has a date-shaped string inside its own narration that the old regex
  mistook for the transaction date). Added a second layout parser
  (`parse_statement_text` / `_parse_two_date_line`) that: infers the year
  from any full date elsewhere in the document; takes the *first* trailing
  decimal number as the amount and the *last* as the running balance,
  ignoring tax/fee columns in between; and guards the number regex
  (`(?<![\d.])...(?![\d.])`) so a dotted date embedded in narration text
  (e.g. "03.06.26") can never be mistaken for a decimal amount. Falls back to
  the original single-date layout when the two-date pattern doesn't match.
  All 32 lines now parse correctly; bank net movement ties to the
  statement's own opening/closing balance within 0.005% (the residual is the
  ITF tax column, correctly excluded as it's not a loan cash flow). Verified
  end-to-end against a real Peru vehicle-loan tape + this statement: tape
  reports disbursements 4,160,066.73 PEN / collections 2,695,720.59 PEN;
  the statement's one large "OP FX PF LEASY II" transfer (5,430,000.00 PEN)
  is the disbursement-funding candidate, correctly flagged by reconciliation
  as a 24% variance against the tape (this tape is a canceled-contracts
  subset, not the funded batch, so an exact match isn't expected). Added
  `tests/test_extract.py` (4 cases, including the embedded-date guard).
- `app/streamlit_app.py`: replaced deprecated `use_container_width=True`
  with `width='stretch'` (the former is past its removal date in the
  installed Streamlit version and was logging a warning on every render).
- `phase3_anomaly_reporting/reporter.py` / `app/streamlit_app.py`:
  `build_report()` never persisted `run.inputs` to the JSON report — only
  `run_id`/`status`/`aggregates`/`exceptions`. So reloading a run via the
  sidebar "Load a run" picker lost the has_tape/has_ledger/has_independent
  flags, and the Results tab's `.get(key, True)` fallback (meant only for
  genuinely pre-fix runs) made every reloaded run look fully populated —
  e.g. `reported_cash_total: 0` rendered as a real "0.00" instead of "no
  data uploaded", even when no ledger was ever uploaded. Added `"inputs":
  run.inputs` to the persisted report and restored it in `_load_run()`;
  verified with a save->reload round trip that `has_ledger: False` survives.
- `app/streamlit_app.py`: the sidebar's "Load a run" selectbox had no `key`,
  so Streamlit remembered the last-selected past run across every rerun —
  once picked, it silently reloaded (and overwrote) `active_run` on *every*
  subsequent script rerun, including right after a fresh "Run verification"
  click. A fresh run would flash once, then get clobbered by the stale past
  run on the next interaction. Fixed by keying the selectbox and gating the
  load behind an explicit "Load selected run" button (only fires on the
  rerun where it's actually clicked, not on every rerun after); a fresh run
  also snaps the picker back to "(current session)" so the sidebar can't
  silently override it going forward.
- `app/streamlit_app.py`: that "snap back" wrote `st.session_state["run_picker"]`
  directly from the Run-tab button handler, which crashed with
  `StreamlitAPIException` — a widget's own state key can only be set before
  that widget is instantiated in a script run, and the sidebar (which owns
  `run_picker`) always renders first. Fixed with the standard deferred-flag
  pattern: the button handler sets a plain `_reset_run_picker` flag instead;
  it's consumed (and only then does it touch `run_picker`) at the top of the
  script, before the sidebar widget exists for that run.
- `phase1_ingestion_parsing/extract.py` / `app/streamlit_app.py`: "cash total"
  was computed as `cash_in + cash_out` (sum of every transaction amount,
  both directions) for the bank/mobile side — i.e. gross transaction volume,
  not a balance. But A5/B3 "cash balance verification" means ledger balance
  vs. bank statement balance *as of the cut-off date* — two point-in-time
  figures, never transaction volume. The BBVA statement actually carries its
  real running balance (the trailing "SALDO CONTABLE" column,
  `_parse_two_date_line` was already parsing and discarding it) — added
  `extract_statement_balance`/`extract_pdf_balance` to recover the *closing*
  balance instead of summing rows. `_run_pipeline` now sources
  `calculated["cash_total"]` from summed statement closing balances (when
  extractable) rather than `indep_agg["cash_total"]`; a new
  `has_independent_balance` flag gates the cash reconciliation separately
  from `has_independent` (which still gates collections/disbursements) —
  a CSV/Excel bank export or a single-date PDF layout has no balance column
  to recover yet, so cash correctly shows "no data uploaded" there instead
  of a wrong number. Verified against the real BBVA statement: extracted
  closing balance 4,610,508.77 matches the statement's own footer exactly.
  Added 2 tests to `tests/test_extract.py`.
- `phase1_ingestion_parsing/ingest.py`: CSV/Excel bank/mobile statements had
  the same two failure modes tabular tape ingestion originally had: (1) the
  generic schema expects a literal, case-sensitive `date`/`amount` column, so
  a real export (e.g. `Date`/`Debit (-)`/`Credit (+)`) parsed to 0 rows; and
  (2) `openpyxl` was never installed, so any `.xlsx` upload (tape, ledger,
  bank, mobile — all of them) crashed with `ModuleNotFoundError` before even
  reaching that. Also found `_read_tabular`'s hardcoded `header=1` for Excel
  is itself fragile — a real sample needed `header=2` (title row + spacer
  row above the header); a fixed offset is wrong for any file shaped
  differently. Fixed all three: added `openpyxl` to requirements.txt;
  replaced the fixed Excel header offset with keyword-driven auto-detection
  (`_read_excel` scans the first 10 rows for one containing recognized
  column-role keywords); added `detect_bank_schema`/`normalize_bank_row`
  (case-insensitive column detection, debit/credit-split or single-signed-
  amount shapes) wired into `_load_file` for bank/mobile sheets, falling
  back to the old simplified schema when nothing is detected; added
  `extract_tabular_balance` (mirrors `extract_pdf_balance`: reads the last
  non-null value under a detected balance column as the closing balance) and
  wired it into `app/streamlit_app.py`'s `_run_pipeline` alongside the PDF
  path. Verified against `Test docs/bank_transactions_with_codes.xlsx`
  (a real Apex Sterling Bank export with title rows + a debit/credit/running-
  balance layout): 25/25 rows, credit total 12,466.98, debit total 889.93,
  closing balance 17,009.15 — all exact matches. Added
  `tests/test_bank_schema.py` (7 cases, including a synthetic title-row
  `.xlsx` round trip for the header auto-detection).
- Three gaps from the read-only gap review (2026-08-19 analysis session,
  above) closed:
  - **PDF confidence gating (B3) was dead code.** `assess_confidence()`
    existed but nothing called it, so an unmapped PDF layout parsed to 0
    rows with no flag. `extract_pdf()` now returns a confidence:0.0
    placeholder row when text exists but no line matches a known layout
    (previously only the scanned/image-only case did this); `_run_pipeline`
    calls `assess_confidence` on bank rows and the Results tab surfaces a
    warning listing every record that needs a manual spot-check. Added 3
    tests to `tests/test_extract.py`.
  - **Exceptions never carried evidence.** `ExceptionItem.evidence` was
    always `[]` — no exception linked back to the row(s) that produced it,
    so "working papers with attached supporting evidence" (B2) wasn't real.
    All four `anomaly.py` rules now attach the canonical record `key`(s)
    (duplicate -> both rows; round-dollar/volume -> the one row;
    round-trip -> both the disbursement and collection row); `reporter.py`'s
    markdown output now prints evidence alongside each exception. Verified
    on the real Peru tape: `L958P9N2`'s finding now carries its exact
    source key. Updated `tests/test_anomaly.py`'s assertions to check
    evidence, added a volume-anomaly evidence test.
  - **Cash-map ingestion was wired to the wrong model.** `SHEET_CASH_MAP`'s
    `DEFAULT_SCHEMAS` entry assumed a cash map is a transaction ledger
    (date/amount columns) — checked the real ones in `Cash Mapping/`
    (`cash_map_Leasy_2026-08-13.txt` etc.) and they're filled-out
    questionnaires (business model, account structure, signatories,
    cash-flow routing — free text, no amounts to parse). Removed the wrong
    schema entry; the app now accepts a cash map as a **reference
    attachment** — saved alongside the run's other uploads and named in
    `run.inputs`/the Results tab, explicitly not parsed for numbers, rather
    than pretending to derive figures from a document that has none.

## In progress / TODO (tracked per phase below)

### Phase 0 — Foundations
- [x] config loader (reads config.yaml), config dataclasses
- [x] run log (append-only jsonl) + models

### Phase 1 — Ingestion & PDF parsing
- [x] multi-format ingest/normalizer (loan tape, bank, mobile money, ledger, cash map)
- [x] methodology (A5 definitions)
- [x] PDF extraction + extract_prompt.txt, confidence scoring
- [ ] PDF row parser currently matches a generic `date desc amount` layout only;
      the real Absa statement's layout is not yet mapped (rules-first: falls back
      to spot-check/LLM prompt). Flag for Phase-2 hardening with format-specific
      rules or OCR.

### Phase 2 — Verification engine (deterministic)
- [x] calculate.py (independent aggregates)
- [x] reconcile.py (B3 thresholds + exception flags) + tests

### Phase 3 — Anomaly detection & reporting
- [x] anomaly.py (rules) + anomaly_prompt.txt (LLM narrative, D3)
- [x] rank.py (risk ranking, 0.75 forensic route)
- [x] reporter.py (working papers + exception report)

### Phase 4 — Human review & sign-off
- [x] approval.py (state machine) + review log
- [x] Streamlit review UI (`app/streamlit_app.py`), reusing the root
      `streamlit_design` skill tokens (theme.css/masthead/cover) for visual
      parity with `Bank Access/app/streamlit_app.py`. Three tabs: Run
      verification (upload tape/bank/ledger/mobile -> pipeline -> results),
      Review & sign-off (approve/reject with reviewer+note -> Head-of-Risk
      sign-off, all through `approval.py` so the state machine has one
      implementation), Audit log & hardening (log tail + hardening entries +
      manual promotion form). Sidebar can reload any past run from `out/<id>/`.
      Smoke-tested: `py_compile` clean, booted headless on :8517, HTTP 200,
      no server-side tracebacks.

### Phase 5 — Feedback & hardening
- [x] harden.py (rule promotion + hardening log) + log wiring
- [x] `promote_to_rule` reachable from the Streamlit audit tab (manual entry
      form); `record_feedback` still unwired to any UI trigger.

## Remaining / open items
- Fill real `.env` for any LLM provider used by D3 steps (rules-only works without).
- Decide on OCR engine if scanned statements are in scope (default: text-layer PDFs).
- Map format-specific bank-statement layouts (e.g. Absa) to production parsing rules
  — two layouts now handled (single-date, two-date-column/BBVA-style); an
  unmapped layout now at least flags for spot-check instead of silently
  returning nothing (see B3 confidence-gating fix above), but isn't parsed.
- Streamlit app has no auth — fine for the 3 named actors on a trusted
  machine, not for shared/multi-tenant hosting as-is.
- ~~Anomaly rule for round-dollar amounts is noisy~~ — fixed (materiality
  gating + roundness redefinition), see "Fixes during build".
- ~~`extract.assess_confidence` dead code~~ / ~~exceptions never carry
  evidence~~ / ~~`SHEET_CASH_MAP` wired to the wrong model~~ — all three
  fixed, see "Fixes during build". `record_feedback` still unwired to any
  UI trigger; `rank.forensic_route` still never invoked; no LLM client
  anywhere (D2/D3 steps are rules-only, as `Overview_-Verifications.html`
  should say); no FX normalization; no cut-off-date alignment for the cash
  check; asset existence verification remains entirely out of scope.

## Analysis session — 2026-08-19 (read-only gap review vs. Overview_-Verifications.html + Workflow Spec)
Ran: `pytest tests` (22/22 pass with venv python), CLI `runner.py` smoke on
`samples/` (exit 0, JSON+MD report), inspected log/out. Verified working:
deterministic ingest/aggregate/reconcile/anomaly/rank/report, human
approve/reject/sign-off (phase4), jsonl audit trail, real run history.
Gaps found (no code changed):
- No LLM client anywhere (grep: only `provider: str = ""` config); D2/D3 steps
  (PDF LLM fallback, anomaly narrative, "Interactive Auditor Querying"
  output) exist only as `prompts/*.txt` — the HTML record overstates these as
  live. `config.yaml` llm.provider/model empty = rules-only, which is fine but
  the record should say so.
- `extract.assess_confidence` / `pdf_confidence_floor` dead code: never called,
  so the B3 "<85% confidence -> manual spot-check" rule is not operational; a
  text-layer PDF in an unmapped layout (e.g. sample Absa) parses to 0 rows
  with no flag. OCR not implemented (`TESSERACT_CMD` only commented in
  .env.example).
- `rank.forensic_route` dead code (never invoked); evidence lists always `[]`
  — "digital working papers with attached supporting evidence" not realized.
- `record_feedback` unwired (only `promote_to_rule` reaches the UI).
- ~~`SHEET_CASH_MAP` defined in ingest but never used by runner/UI — cash-map
  input (A2) is not ingestible end-to-end.~~ Resolved 2026-08-19: cash maps
  are uploaded, parsed for named accounts, and matched to statements (see
  below) — `SHEET_CASH_MAP` itself stays unused by design (A2 is a
  questionnaire, never a row-shaped ledger).
- No FX normalization: aggregates sum mixed currencies (PEN/KES/USD) with no
  FX lookup, contra the A4 "FX lookups" D1 item.
- Asset existence verification (bulk vehicles/motorcycles vs registries) from
  the spec is entirely out of scope in the build.
- No cut-off-date alignment / bi-weekly schedule concept (cash balance "as of"
  matching is a plain point-in-time sum for one PDF layout only).

## 2026-08-19 — third PDF statement layout (ISO date + running balance, no sign)
- Bug: `Test docs/bank_statement_with_codes.pdf` (a generic US-style statement:
  `YYYY-MM-DD TXN-code description category amount balance`, no debit/credit
  column) matched neither existing layout (dates are 4-digit-year-first, not
  DD-MM/DD-MM), so `extract_pdf` fell back to the unmapped placeholder —
  every calculated figure for that file showed as zero.
- Fix (`extract.py`): added layout 3. `_parse_running_balance_line` pulls
  date/amount/balance/description per line (stripping a leading `TXN-...`
  reference token); `_running_balance_lines` infers each row's direction
  from the balance delta against the previous row, seeded by
  `_find_declared_starting_balance` (reads a "STARTING BALANCE ... ENDING
  BALANCE" summary block when present) with a description-keyword guess
  ("deposit"/"salary"/"credit"/...) as a last resort for the first row only.
  `parse_statement_text` and `extract_statement_balance` both fall back to
  this layout when layouts 1/2 find nothing.
- Verified against the real file: 25/25 lines parsed, deposits sum
  ($12,466.98) and withdrawals sum ($889.93) match the statement's own
  "TOTAL DEPOSITS"/"TOTAL WITHDRAWALS" summary exactly, closing balance
  ($17,009.15) matches "ENDING BALANCE" exactly.
- Added `test_iso_date_running_balance_layout_infers_direction_from_starting_balance`
  and `test_iso_date_running_balance_layout_without_summary_guesses_first_row_direction`
  to `tests/test_extract.py`. Full suite: 35/35 passing. App restarted on
  port 8600 (HTTP 200).

## 2026-08-19 — statement -> cash-map account mapping + per-account aggregates
- Feature (user request): map each uploaded bank/mobile statement to the
  cash-map account it belongs to, show that account's own purpose/role
  writeup, and compute aggregates for just that statement.
- New module `phase1_ingestion_parsing/cashmap.py`:
  - `parse_cash_map(path)` reads `.txt`/`.html`/`.pdf`/`.docx`/`.json` cash
    maps into a common `{borrower_name, accounts, source}` shape.
    Structured JSON (schema_version 2.0+, real `accounts` array with
    bank_name/currency/purpose) is read directly; everything else falls
    back to a free-text parser targeting the "4.2 ... describe its
    purpose, bank, and who has access" answer, tolerant of both real
    layouts seen (Leasy: one "Account N:" per line; First Circle: all
    three run together in one paragraph). Bracketed template labels (e.g.
    "[Account 1: Purpose - Loan disbursements]") are stripped first — they
    echo the question's own field name ahead of the real answer and would
    otherwise parse as a second, fake account.
  - `match_accounts(statement_text, filename, accounts)` narrows to
    accounts whose declared bank name appears in the statement — real cash
    maps mostly carry no account number, so this is deliberately
    best-effort; with no bank name to go on (e.g. Leasy) it returns every
    account as an equally-valid candidate for a human to pick.
  - `.docx` reading needed no new dependency: a docx is a zip of XML, read
    via stdlib `zipfile` + `xml.etree`.
- `app/streamlit_app.py`: `_run_pipeline` now loads each bank/mobile
  statement individually (`_load_statement`, replacing the old
  bank-only/flattened loop) so a per-file aggregate and raw text survive
  for matching; parses every uploaded cash map into `cash_map_accounts`;
  builds `statement_summaries` (per-file cash_in/cash_out/closing_balance +
  match candidates), persisted in `run.inputs` so a reloaded run still
  shows the section. Results tab renders one dropdown per statement
  (pre-populated with the candidate list, defaulting to the narrowed match
  or, if none, every account) plus that account's description and the
  statement's own aggregates.
- Verified against the real Leasy/First Circle/Thai Lending cash-map
  samples directly (no account numbers in any of them, by design) and
  end-to-end via `_run_pipeline` with the real loan tape + BBVA PDF +
  Leasy cash map — 3 candidate accounts surfaced correctly (no bank names
  in that cash map, so no narrowing was possible, as expected).
- Added `tests/test_cashmap.py` (6 tests: both free-text layouts, the
  structured JSON schema, and all three match_accounts branches). Full
  suite: 41/41 passing. App restarted on port 8600 (HTTP 200) — this drops
  any in-progress browser session's uploads again; re-upload before
  re-running.

## Loan-tape dropdown (Redshift) — 2026-08-20
- Added a "1b. Loan tape from Redshift" section in the Run tab of
  `app/streamlit_app.py`: a borrower dropdown, an optional begin-date range,
  and a max-rows cap. Selecting a borrower + "Load loan tape" pulls that tape
  from the sibling Redshift Query API and feeds it into the pipeline as the
  loan tape (alternative to file upload).
- `_run_pipeline` now accepts `tape_records` (pre-fetched raw tape rows); when
  present it derives disbursement/collection events via
  `normalize_loan_tape_row` exactly like the file path. Records `tape_source`
  in run inputs.
- New helpers `_api_get`, `_fetch_borrowers` (cached 5min),
  `_fetch_loan_tape_rows`; API URL/key from `.env`
  (`REDSHIFT_API_URL`/`REDSHIFT_API_KEY`), defaults
  `http://127.0.0.1:8001`.
- Redshift API changes (sibling `redshift-api/`): `/loan-tape` gained
  `date_from`/`date_to` (filter on begin_date, YYYY-MM-DD validated) and the
  `MAX_RESULTS_LIMIT` was raised to 2,000,000 / timeout 120s so large filtered
  tapes can load. The date filter lets a multi-million-row tape (e.g. payjoy
  13.5M, advance 1.3M) be narrowed before transfer.
- Verified: `py_compile` OK; Streamlit AppTest — 0 exceptions, dropdown lists
  all 46 borrowers, selecting leasy + max-rows 3 + Load returns
  "Loaded 3 loan rows for leasy"; fetched records normalize to the same
  disbursement/collection events as an uploaded tape.
- Output note: SMOKE-test Streamlit instance was stopped; user's app instance
  must restart to pick up these changes.


## Loan tape: full output + aggregation summary -- 2026-08-20
- After a borrower's tape is loaded (from the No.1b dropdown), the Run tab now
  renders the FULL loan tape as a dataframe (\"Full loan tape\") plus a summary
  section with aggregations over the raw Redshift records.
- New helper \_loan_tape_summary(records)\ in app/streamlit_app.py: coerces
  the string money columns and computes -- loan count; total principal, total
  loan amount, principal/interest/penalty outstanding; total outstanding;
  collected to date (total - outstanding, floored at 0); counts by status and
  branch; and delinquency buckets (DPD>0, 30-59, 60-89, >=90).
- Rendered as metric cards + by-status/by-branch count tables, then the full
  tape table (row x column caption).
- Verified: py_compile OK; \_loan_tape_summary\ exercised against real Leasy
  staging-row samples -- money totals, outstanding, collected-to-date, status/
  branch counts, and DPD buckets all correct.


## PDF extraction: positional table parsing + metadata + balance tie-out -- 2026-08-20

Closed the biggest "Partial" gap from the workspace review: the Absa/Mono-style
statement (`samples/statement_absa.pdf`) previously returned a single
confidence:0.0 placeholder. Root cause: the flattened text layer can't resolve
it — month-name dates (`June 23rd 2022`), multi-line narrations, and *unsigned
amounts whose direction lives in which column* (DEBIT vs CREDIT). That
information only survives in the word coordinates, which `page.extract_text()`
throws away.

Changes in `phase1_ingestion_parsing/extract.py` (all deterministic, no new deps):

- **Layout 4, positional table parsing.** `_page_lines()` groups
  `page.extract_words()` into visual lines keeping each word's `x0`/`x1`;
  `_table_header_columns()` detects the `# DATE NARRATION DEBIT CREDIT BALANCE`
  header (exact words, in order, so flat layouts & the page-1 monthly summary
  can never false-positive) and derives per-column x-ranges from the header
  word positions; `_scan_numbered_table()` maps every data-line token back to
  its column — direction from DEBIT vs CREDIT, narration from NARRATION,
  multi-line narration coalesced (prefix before the first row, suffix after),
  month-name dates normalised to ISO, balance column kept per-row as evidence.
  Registered via a header-driven spec, so a new bank layout = a new spec, no
  scanner changes.
- **`detect_shape(text)`** reads header metadata: bank, account number,
  currency, statement period, available balance, declared total debits/credits
  — including the Mono two-line `Available balance / Total debits / Total
  credits` block (figures associated with labels by order). Feeds layout
  selection + tie-out; surfaced in the Streamlit Results tab.
- **Granular confidence via balance tie-out** (replaces the binary 1.0/0.0):
  `_tie_out()` reconciles parsed rows against the statement's own declared
  figures — Case A: summed debits/credits vs declared "Total debits/credits"
  (numbered-table); Case B: opening + net == closing (ISO/running-balance).
  Match -> confidence 1.0; mismatch -> 0.5 so B3 flags a spot-check. The
  two-date (BBVA) layout is deliberately excluded (its ITF/fee column sits
  between amount and balance, so opening+net never equals closing there).
- `extract_pdf()` prefers layout 4, then flat layouts 1-3, then the 0.0
  placeholder; `extract_pdf_balance()` now falls back to the positional
  last-row balance when the text layer has no running-balance layout.
- `app/streamlit_app.py`: `_load_statement` carries `shape` per statement
  (persisted in `run.inputs["statement_summaries"]`), and the Results
  statement->cash-map section now shows the detected bank/currency/layout.
- The `Path(path).exists()` guard keeps the positional attempt off the unit
  tests' mocked (nonexistent) paths, and a try/except in the scanner makes any
  unreadable PDF fall through to the flat-text path instead of crashing.

Verified against the real samples:
- Absa: **15/15 rows**, correct direction/dates; summed credits 4,672,790.16 and
  debits 4,015.00 **tie exactly** to the statement's declared totals -> all
  confidence 1.0 / tie_out "ok"; `extract_pdf_balance` = 2,003.94 (the last-row
  running balance). Full CLI `runner.py` on the PDF: 15 transactions ingested,
  independent collections/disbursements match the statement's own totals.
- BBVA two-date: unchanged (32 rows, conf 1.0, closing 4,610,508.77).
- APEX ISO/running: unchanged (25 rows, deposits $12,466.98 / withdrawals
  $889.93 / closing $17,009.15; now also tied out via opening+net==closing).
- Scanned PDF: unchanged (0.0 placeholder flagged for spot-check; OCR still out
  of scope per the 2026-08-20 decision).
- Suite: 41 -> **48 tests** (added month-name dates, detect_shape, tie-out
  pass/fail, failed-tie-out flagging, real-file Absa end-to-end, summary/totals
  not parsed as transactions). 48/48 green with the venv interpreter.
  Streamlit AppTest: 0 exceptions, all 3 tabs render.


## 2026-08-25 — connecting four existing building blocks (feedback, coverage, lead time, borrower recency)
Per user request: wire up features that had working building blocks but no call sites / aggregation views. All additive — no signatures changed, no keys renamed.

1. **Feedback loop closed** (`app/streamlit_app.py` Review tab): `record_feedback()` (phase5) was fully implemented + logged but nothing called it. Approve/reject paths now also call `record_feedback(RUN_LOG, exception_pattern(item), "approved"|"rejected", reviewer)` next to `approval.approved()/reject()`. New `harden.exception_pattern(item)` derives the rule/category from the exception id (`<run_id>:<kind>:<rule>[:<n>]`, kind shorthand `recon`/`anom` — e.g. `...:anom:roundtrip:3` -> "roundtrip", `...:recon:collections` -> "collections"), exported from `phase5_feedback_hardening/__init__.py`.
2. **Population coverage** : `population_size` + `coverage_pct` added to `run.inputs` in both `runner.py` and `streamlit_app.py` `_run_pipeline` via new `phase0_foundations/metrics.coverage(rows_ingested, population_size)` (0/0 -> 100.0 vacuous). `reporter.build_report()` now renders a `## Coverage` section in the `.md` ("N of N transactions tested (X%)") and a structured `coverage` key in the `.json`. Today no confidence-floor rows are dropped, so ingested == population (100%); the fields make the distinction explicit and future-proof.
3. **Lead-time metric**: new `phase0_foundations/metrics.py` — `lead_times(log)` matches each `run_started` ts to the run's **last** `sign_off` ts (run_id derived from the sign_off `exception_id` prefix, since sign_off events carry only exception_id), returning `{run_id, started, signed_off, elapsed_seconds}` (None when never signed off). Surfaced in the Streamlit Audit tab as a run_id/started/signed_off/elapsed table (human-readable duration via `_format_duration`).
4. **Last verification per borrower**: `_run_pipeline` now records `"borrower"` in `run.inputs` (from `st.session_state["tape_borrower"]` when the tape came from the dropdown, else `"uploaded"`). New `metrics.last_verification_per_borrower(log, out_root)` reads all `run_completed` events, resolves each run's borrower from its persisted report inputs, keeps the most recent run per borrower, and sorts oldest-first — surfaced as a new small section in the Audit tab so overdue borrowers sit at the top.

Tests: new `tests/test_metrics.py` (10 tests: coverage full/partial/empty-population, exception_pattern recon/anomaly/fallback, lead times matched + not-signed-off, per-borrower latest + sort, uploaded fallback). Suite: 48 -> **58 passed** with the venv interpreter; `py_compile` clean on all changed modules; CLI `runner.py` smoke verified the "19 of 19 transactions tested (100.0%)" line + JSON `coverage`; Streamlit AppTest boots with 0 exceptions and the new Audit sections render.


## 2026-08-25 — asset existence verification, wired into the pipeline

Per user request: bring asset existence verification (the sixth area named in the workflow spec's scope) into the same deterministic reported-vs-independent pattern as collections/disbursements/cash, instead of leaving it as a standalone script disconnected from everything else.

**What was found first**: the Peru plate-registry checker (`text.py`, root) was a fully working semi-automated tool (drives Chrome against placas.pe, solves the reCAPTCHA checkbox, writes results to Excel), but it lived at the repo root, misplaced relative to the `vehicle_plate_peru/` folder it clearly belonged in — which turned out to be a broken git submodule (a gitlink to commit `8857851...` with no `.gitmodules` entry anywhere in this monorepo's history, so the referenced commit is unrecoverable and the folder always read as empty). Converted it to a regular tracked directory via `git mv text.py vehicle_plate_peru/checker.py`; nothing of value was in the orphaned gitlink to lose.

**Integration** (mirrors reconcile.py's reported/calculated split, not a new pattern):
1. `phase1_ingestion_parsing/assets.py` — `load_expected_assets()` reads a reported collateral register (plate/borrower/expected-owner, columns detected by keyword like `ingest.py` does for bank statements); `load_registry_results()` reads the checker's output Excel by column name (`Status`/`Propietario`/`Estado`/`Marca`/`Modelo`/`Verified Date`), keyed by plate, no import dependency on `vehicle_plate_peru` at all — the Excel column names are the only contract, same as any bank statement.
2. `phase2_verification_engine/assets.py` — `verify_asset_existence(expected, registry_results, run_id)` returns `ExceptionItem`s (`kind="asset"`) for three deterministic findings: `not_checked` (reported asset, no registry result — severity 0.9), `check_failed` (registry lookup errored or found nothing — severity 0.7), `owner_mismatch` (registered owner doesn't match the name on file, case/whitespace-insensitive — severity 0.85).
3. `harden.exception_pattern()` extended to recognize the `asset` id-shorthand alongside `recon`/`anom`, so asset exceptions get the same per-rule feedback granularity (`not_checked`/`check_failed`/`owner_mismatch`) rather than collapsing to the generic `kind`.
4. `reporter.py` renders a second coverage line ("N of M reported assets checked against the registry (X%)") alongside the existing transaction-coverage line, and the JSON report's `coverage` key gains an `assets` sub-object — additive, no existing keys touched.
5. `runner.py` gained `--assets`/`--asset-checks` CLI args; `streamlit_app.py` gained a "1c. Asset existence verification (optional)" upload section (reported register + registry-check-results uploaders) and an "Asset existence verification" results table. Asset exceptions merge into the same `rank_exceptions()` call as recon/anomaly, so they flow through Review & sign-off and the Audit tab exactly like any other exception — no new UI plumbing needed there.

Tests: new `tests/test_assets.py` (11 tests: keyword column detection, NaN-safe empty-cell handling for both CSV and Excel round-trips — a real bug caught by the CSV test, fixed via a shared `_clean_str()` helper — later-file-wins on a re-checked plate, and all three exception rules incl. the owner-match/no-expected-owner-on-file non-firing cases). Suite: 58 -> **69 passed**. CLI smoke-tested end-to-end with a 3-asset fixture (one clean, one owner-mismatch, one not-checked) — report correctly showed "2 of 3 reported assets checked (66.7%)" and flagged exactly the two expected exceptions. Streamlit AppTest: 0 exceptions on boot; live server restarted and reachable (HTTP 200) on the updated code.

**Still not integrated** (unchanged from the workflow-spec gap list): the checker itself remains one-plate-at-a-time with a manual CAPTCHA-image pause, so a full portfolio run is still a loop over a script, not a bulk verification — this work connects its *output* to the pipeline, it doesn't make the *check itself* bulk or unattended.


## 2026-08-25 — bank reconciliation upgrade, ported from vehicle-verification (github.com/nephy-edge/vehicle-verification)

**Confirms the previous entry's guess was wrong in one detail**: that repo is not an unrelated project — its single commit (`8857851...`) is exactly the commit the broken `vehicle_plate_peru` gitlink pointed to, so it's the actual recovered content, not a coincidence. Beyond the plate-verification scripts, it also contains a second, independently-built implementation of this same workflow (its own `ingest.py`/`calculate.py`/`reconcile.py`/`streamlit_app.py`, citing the same `Overview_-Verifications.html` spec) with two capabilities ours lacked: real OCR for scanned PDFs, and transaction-level matched/unmatched reconciliation (vs. our aggregate-variance-only checks). Per user request, ported both — layered in alongside the existing validated logic, not replacing it (its rules-based parsers are tie-out-verified against real Absa/BBVA/APEX samples; the ported OCR/matching code hadn't been validated against those same samples, so discarding proven behavior for unproven behavior would've been a downgrade, not an upgrade).

**1. OCR fallback (`phase1_ingestion_parsing/ocr.py`, new)**: ported the render+preprocess+Tesseract steps (`pypdfium2` page rendering, grayscale/contrast/sharpness preprocessing, common-misread fixes) but *not* the original's separate narrow line-parser (`Date Transaction-Code Description Amount Balance`, US-personal-banking-shaped). Instead, `extract.py`'s `extract_pdf()` now tries OCR only when the text layer is genuinely empty (a real scan), then feeds the OCR'd text through the *same* `parse_statement_text()`/`detect_shape()`/tie-out pipeline already validated for every other PDF — OCR changes how the text is obtained, never how it's interpreted, so a scanned copy of a known layout still goes through trusted logic instead of a second parser. OCR rows are capped at confidence 0.6 regardless of tie-out (always below the 0.85 B3 floor — OCR carries its own error class a clean tie-out can't rule out). Degrades to the existing 0.0-confidence placeholder (with an updated, more specific reason string) when `pytesseract`/`pypdfium2` aren't installed or the `tesseract` binary itself isn't found — confirmed via `tesseract_available()`, which returns `False` on this machine (no `tesseract.exe` installed), so this is verified-degrading, not verified-working, until that binary is added. `.env.example`'s pre-existing `TESSERACT_CMD` stub is now actually read.

**2. Transaction-level matching (`phase2_verification_engine/transaction_match.py`, new)**: ported `normalize_value`/`match_records`/`create_reconciliation_report`, adapted to match on this project's canonical `description` field (exact, then substring/partial) instead of an arbitrary Excel/PDF column — neither our loan-tape rows nor our bank rows carry the free-form "transaction code" column the original matched on, and building a second raw-parsing path to get one would have duplicated validated ingestion rather than reusing it. Exposed as a new **"Transaction matching"** Streamlit tab (4th tab) — two file uploads, a match/unmatched breakdown, and an Excel download (`build_match_report`) — deliberately *not* wired into the automatic `rank_exceptions()` flow: unlike a registry lookup (asset verification) or a threshold breach (reconcile.py), most real statement pairs will share little text verbatim, so a low match rate here is the expected case, not an anomaly worth flooding the review queue over. `to_exceptions()` exists for a caller that explicitly wants unmatched records in the queue anyway; `harden.exception_pattern()` extended with a `match` id-shorthand for when it's used.

**Verified**: end-to-end smoke test (real tape.csv + bank.csv fixtures, via the actual `load_and_normalize`/`match_transactions`/`build_match_report` call chain) correctly partial-matched a bank narration referencing a loan id and correctly left the other two rows unmatched on each side. New tests: `tests/test_ocr.py` (4) + 2 more in `tests/test_extract.py` (OCR-success reuses existing layouts and caps confidence; OCR-failure still degrades to the placeholder) + `tests/test_transaction_match.py` (9: exact/partial/no-match, one-to-one matching guarantee, exceptions, report shape). Suite: 69 -> **84 passed**. Streamlit AppTest: 0 exceptions on boot with the new tab; live server restarted and reachable (HTTP 200).

**Not done**: `pytesseract`/`pypdfium2`/`pillow` added to `requirements.txt` but the `tesseract-ocr` system binary is not installed on this machine, so the OCR path is code-complete and tested-for-graceful-degradation only, not tested-for-actual-OCR-accuracy — that needs the binary installed and a real scanned sample run through it. The vehicle-verification repo's Peru/UK plate-verification scripts (a more capable, multi-generation version of what's in `vehicle_plate_peru/checker.py`) were not pulled in — out of scope for this request, which was specifically the bank reconciliation half.


## 2026-08-25 — correction: asset existence verification moved to its own tab

User feedback on the earlier entry's UI placement: asset existence verification had been added as a "1c." subsection *inside* the Run tab, sharing its "Run verification" button and merging asset exceptions into the same `rank_exceptions()`/`run.exceptions` as collections/disbursements/cash. Correct per the user: "you should have retained the vehicle section as it was and create new tabs for the additional things" — i.e. match the `Transaction matching` tab's already-correct pattern (fully separate: own uploaders, own button, own result), not fold it into the main pipeline.

Reverted: `_run_pipeline()`'s `asset_files`/`asset_check_files` params and all asset logic inside it (ingestion, `verify_asset_existence` call, merge into `exceptions`, `asset_coverage`/`asset_summaries` in `run.inputs`) — back to its pre-asset-work shape for the tape/bank/mobile/cash flow. Removed the "1c." upload section and the asset results block from the Run tab.

Added instead: a new standalone **"Asset existence verification"** tab (2nd tab, between Run and Review) — the same two uploaders and the same `load_expected_assets`/`load_registry_results`/`verify_asset_existence` calls, but wired directly (not through `_run_pipeline`) with its own button and its own `st.session_state["asset_result"]`, showing the summary table and an exceptions table inline. It does not touch `RUN_LOG`/`active_run`/Review & sign-off — a deliberate parallel to `Transaction matching`'s existing standalone design, not an oversight.

Not touched: `runner.py`'s CLI `--assets`/`--asset-checks` flags (a CLI has no "tabs" to conflate — the complaint was UI-specific) and the underlying `phase1_ingestion_parsing/assets.py`/`phase2_verification_engine/assets.py` modules, which are unchanged and still fully tested by `tests/test_assets.py`.

Verified: full suite still **84 passed** (no logic removed, only call sites moved), `py_compile` clean, Streamlit AppTest 0 exceptions with 5 tabs now present, live server restarted and reachable (HTTP 200).


## 2026-08-25 - vehicle verification tab (registry API scaffold)

Confirmed first (per user: "confirm if we use it" before removing the Selenium checker): `vehicle_plate_peru/checker.py` is **not used as code anywhere** - no import, no `python -m` invocation, no scheduler, in the whole workspace. The only references from the pipeline are prose (docstrings in `assets.py`, the asset-tab caption, `runner.py` help text) plus its *output contract* (`Status`/`Propietario`/`Marca`/`Modelo`/`Verified Date` columns) which `load_registry_results` consumes generically by column-name keyword. The checker is a manually-run, CAPTCHA-paused, one-plate-at-a-time script; the deterministic pipeline never executes it. `_test_recon.py` imports `vehicle_plate_peru.reconciliation` - a module that does not exist in the folder (stale probe, unrelated to checker.py).

User chose to replace it with the Verifik-style API approach, scoped as a **scaffold with a plugged interface** (no guessing the real wire format; mock by default, live client behind a documented seam).

**Added**: new `phase1_ingestion_parsing/vehicle_verify.py` - a pluggable registry-API client with:
- `COUNTRY_CONFIG`: 7 countries (PE MX CO CL AR BR EC), each with a primary endpoint, a field map (source field -> canonical Plate/Brand/Model/Year/VIN/Owner/Color/Status), optional `extra_inputs` (Colombia: document_type + document_number) and `extra_endpoints` (Chile SOAP status lookup).
- `flatten_json`: recursive flatten of nested JSON, first/shallowest-occurrence-wins on the normalized leaf field name, so each country's differing nesting resolves cleanly.
- `apply_field_map`: maps flattened leaves onto the canonical set (Colombia: RUNT `modelo`->year, `linea`->model).
- `VehicleClient` protocol + `MockVehicleClient` (canned per-country nested responses, no credentials) + `VerifikClient` (reads `VERIFIK_TOKEN`/`VERIFIK_BASE_URL`; `_call` left as a documented NotImplementedError seam - the wire format is unguessable). `make_client()` flips to live.
- Bucketing: `classify_match`/`classify_expected` -> Match / Partial Match / Mismatch / No data (string containment either direction = partial).

**New tab** "Vehicle verification" (3rd tab, after asset existence): country selector, Single-plate vs Excel upload, live preview + column pickers (plate column, optional expected-vehicle column), per-country extra inputs shown only when relevant, Run button, results table + Found/Not-found/Errors metrics, mismatch-only "Issues Found" table, and an Excel download appending a "Verification Results" sheet (same Verified-Date-stamped pattern).

**Verified**: `tests/test_vehicle_verify.py` (10 new, plain-assert style): shallowest-wins flatten, Colombia year/model mapping, all 7 countries round-trip plate, extra-inputs declaration, all four bucketing outcomes, download column shape. Full+existing `tests/test_assets.py` still 11 passed; app/module `py_compile` clean; Streamlit boots headless and serves HTTP 200 with no log exceptions; tab-block identifier resolution checked.

**Known open**: the real Verifik request/response contract and token are still required before `VerifikClient` goes live (mirrors the upstream repo's `reconciliation.py` the user described but hasn't supplied). Mock is wired so the section is fully usable and testable today.


## 2026-08-26 — prepping for a standalone repo + Streamlit Cloud deploy

Per user request to create a repo and deploy this app. Confirmed with the user first (per the same "never guess" discipline as the vehicle-verification entry) rather than pushing straight to a new public repo:
- **Real sample data**: `samples/statement_absa.pdf` (a real bank statement, its balances independently verified against real figures per earlier entries) and `samples/loan_tape_ph.csv` are real financial data, not synthetic fixtures — user chose to exclude them from the repo entirely (`samples/` added to `.gitignore`; the tests that use them already skip gracefully when the file is absent, e.g. `test_numbered_table_full_parse_of_real_absa_sample`).
- **Visibility**: private repo, per user's choice.
- **Execution**: no `gh` CLI or stored GitHub/Streamlit credentials were available in this environment — user is authenticating (`gh auth login` or a PAT) so repo creation + push can happen via CLI; actual Streamlit Community Cloud app creation still needs the user's own browser session (OAuth-only, no public API for that step).

**Found and fixed a real deploy-breaking bug while prepping**: `app/streamlit_app.py`'s masthead/theme styling was reading three small files from `Verifications/.kilo/skills/streamlit_design/assets/` — a path *outside* `verifications-automation/`, only reachable because the app happens to run inside the parent monorepo today. A standalone repo (the whole point of this request) would 500 on startup the moment it tried to read that path. Vendored the three files into a new `verifications-automation/design_assets/` and pointed `_SKILL_ASSETS` at the local copy instead. Re-verified: full suite still 94 passed, `py_compile` clean, AppTest 0 exceptions.

**Also while here**: added `packages.txt` (`tesseract-ocr`) so OCR actually runs live once deployed on Streamlit Community Cloud's Debian environment — the one thing this local Windows dev machine can't test (no `tesseract.exe` installed here; genuinely exercising OCR accuracy, not just its graceful-degradation path, now becomes possible once deployed). Added a "Deployment" section to README.md covering the no-secrets-needed-for-a-first-deploy posture (Redshift dropdown and the live Verifik client both already degrade to a clear error/mock rather than crashing without their env vars).

**Also resolved** (from a mid-conversation "how do I connect redshift" question): `redshift-api`'s `.env` was already fully configured with a real `DATABASE_URL` + `API_KEY` — the loan-tape dropdown wasn't broken, the service just wasn't *running*. Started it (`uvicorn app.main:app --host 127.0.0.1 --port 8001` — note the README's own example uses port 8000, but `verifications-automation/.env`'s `REDSHIFT_API_URL` expects 8001) and confirmed real end-to-end connectivity: `/health/db` → `{"database":"ok"}`, `/borrowers` → a real 46-borrower list from the warehouse.

**Not done yet**: the actual `gh repo create` + push + Streamlit Cloud app creation, pending the user completing authentication.
