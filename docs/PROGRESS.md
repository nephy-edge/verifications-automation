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

**Deploy completed**: user ran `winget install GitHub.cli` + `gh auth login` (browser flow, account `nephy-edge`). Since `verifications-automation/` lives inside the `projects` monorepo (which had a large amount of unrelated in-progress staged/unstaged changes across other subprojects at the time — Lending, Rental2, Tableau_Analytics), git history was **not** initialized in place to avoid entangling this push with that unrelated work. Instead: robocopied this folder to a clean standalone location (`C:\Users\NephyNdwiga\Downloads\verifications-automation`, excluding `.venv/`, `__pycache__/`, `.pytest_cache/`, `samples/`, `out/`, `.env`, and the runtime log files), verified no secrets/samples/venv leaked into the copy, then `git init` + one commit + `gh repo create nephy-edge/verifications-automation --private --source=. --remote=origin --push`. Confirmed via `gh repo view`: private, pushed, default branch `master` (not `main` — left as-is, cosmetic only). Redshift connectivity issue found post-deploy (`Connection refused` from Streamlit Cloud secrets pointing at `127.0.0.1:8001` — that loopback address means Streamlit Cloud's own container, unreachable from the internet) was bridged temporarily with a Cloudflare quick tunnel (`cloudflared tunnel --url http://127.0.0.1:8001`, no account needed, no ngrok browser-warning-page problem) — user chose this over a proper `redshift-api` cloud deployment for now; documented as fragile (URL changes on tunnel restart, requires this machine + tunnel + `redshift-api` to stay running).

## 2026-08-26 — merged Asset existence verification + Vehicle verification into one tab

User noticed the overlap directly: both tabs answered "is this vehicle/owner what we claim it is," but via disconnected mechanisms — Asset existence verification (`phase2_verification_engine/assets.py::verify_asset_existence`) required a manually pre-run, separately-uploaded registry-check file, while Vehicle verification (`phase1_ingestion_parsing/vehicle_verify.py`) did its own live/mock registry-API lookup but only produced a standalone downloadable spreadsheet — not `ExceptionItem`s in the Review/Audit queue. Vehicle verification's own docstring already said it was built as a replacement for the browser-scraper (`vehicle_plate_peru/checker.py`) that produces exactly the registry-check file the other tab expects, so the two were never meant to stay separate long-term.

**What changed** (`app/streamlit_app.py`, `tab_assets`/`tab_vehicles` merged into one `tab_assets` — tab count now 5, not 6): one tab, "Asset & vehicle verification", with a shared country selector + per-country extra-input fields (Colombia's document type/number, etc.) at the top, then a mode radio:
- *Verify a reported asset register* — the old Asset existence flow, but the registry side now defaults to a **live lookup** (`vehicle_verify.make_client().lookup()` per plate, mapped into the exact dict shape `load_registry_results` used to produce: `status`/`propietario`/`marca`/`modelo`/`verified_date`) instead of requiring a second file upload. The old "upload a pre-run registry-check file" path is kept as an explicit fallback radio option, not removed — someone who already ran a check elsewhere (or wants to avoid live API calls) still can. Findings still flow into `verify_asset_existence()` -> the same Review/Audit sign-off queue as before; brand/model are now also shown in the summary table (informational — `verify_asset_existence`'s three rules are unchanged: `not_checked`/`check_failed`/`owner_mismatch`, still owner-only, no new exception types added, to keep this a mechanism merge rather than a rules-scope expansion).
- *Quick lookup / spot check* — the old Vehicle verification tab's Single-plate/Excel-upload flow, verbatim, for ad hoc checks with no formal register (kept because it's a genuinely different job: one-off spot check vs. bulk collateral audit).

Both modes now share the same `country_code`/`asset_extra_inputs` state and the same `make_client()` call site — the actual "merge": one registry-lookup mechanism now backs both the ad hoc UI and the audit exception pipeline, instead of two.

**Verified**: `py_compile` clean, `AppTest` boots with 0 exceptions, full suite still 94/94 passed (no test referenced the old tab structure, so nothing needed updating there). README's tab list updated (five tabs, not six) to describe the merged capability and its two modes.

## 2026-08-26 — Verifik actually goes live (Peru only)

User asked why a plate lookup returned "random data" instead of calling Verifik. Root cause: `make_client()` was hardcoded to always return `MockVehicleClient()` — it never even checked `VERIFIK_TOKEN`, so having a real token in `.env` changed nothing. Separately, `VerifikClient._call()` was still the documented `NotImplementedError` scaffold from the vehicle-verification port, since nobody had confirmed the real wire format yet.

Looked up Verifik's own docs (`docs.verifik.co/vehicle-validation/peru/peruvian-vehicle`) via web search/fetch rather than guessing, and confirmed the real, current contract for Peru:
- `GET https://api.verifik.co/v2/pe/vehiculo/placa?plate=<plate>`, `Authorization: Bearer <token>`
- Response: `{"data": {"plate","use","type","brand","model","year","engineSerial","chasisSerial","seats","validFormat","serial"}, "signature": {...}}`
- **No owner/color/status field on this endpoint at all.** The pre-existing `COUNTRY_CONFIG["PE"].field_map` had been guessed against Spanish field names (`marca`/`modelo`/`propietario`/`estado`/`color`) that don't exist in the real response — so even flipping `make_client()` alone would have silently returned all-blank fields.

Surfaced this gap to the user before implementing (owner-based checks can't be backed by this endpoint; Verifik's separate SUNARP/"Full ID" product might have `propietarios` per search results, but its contract isn't publicly documented and wasn't confirmed) — user chose to go live with brand/model/year now and leave owner verification unavailable for Peru rather than block on chasing that second product.

**Changes** (`phase1_ingestion_parsing/vehicle_verify.py`):
- `COUNTRY_CONFIG["PE"].field_map` corrected to the real English field names (`plate`/`brand`/`model`/`year`/`chasisserial`→vin); owner/color/status intentionally left unmapped (no source data).
- `MockVehicleClient._SAMPLES["PE"]` rewritten to mirror the real response shape (nested under `"data"`/`"signature"`, no owner/color/status) so the mock's gaps match the live gaps instead of a friendlier fiction.
- `VerifikClient._call()`: implemented for real for `PE` only (`urllib.request` GET with the Bearer header, matching the rest of the codebase's HTTP pattern — no new dependency). Every other country still raises `NotImplementedError` with a clear per-row error message, on purpose — still unconfirmed, refuses to guess.
- `VerifikClient.TOKEN`/`BASE_URL` moved from class-level attributes (evaluated via `os.getenv()` at import time) to `@property` (evaluated at call time) — a real bug: `app/streamlit_app.py` imports this module *before* it calls `load_dotenv()`, so the old class-level read would have frozen `TOKEN` empty forever regardless of what's in `.env`.
- `make_client()` now returns `VerifikClient()` whenever `VERIFIK_TOKEN` is set, `MockVehicleClient()` otherwise.

**Verified against the real API, twice**: first with the user's then-current token — got a clean, real `403 token_expired` (decoded the JWT's `expiresAt` claim to confirm: issued 2026-07-13, expired 2026-08-13, 13 days stale). User rotated the token in Verifik's dashboard and updated both `.env` and Streamlit Cloud secrets; re-ran the same live lookup and got a genuine registry hit for a test plate: `TOYOTA TERCEL 1.3, 1992, VIN 13DE3123DE` (owner/color/status correctly blank, matching the confirmed endpoint's real gap, not a bug). `py_compile` clean, full suite still 94/94 (no test asserted on PE's old mock field values). Pushed to the deployed repo.

**Known open**: owner verification for Peru still needs Verifik's SUNARP/Full-ID product contract confirmed (from the user's own account dashboard/API reference, since public docs don't expose it) before `verify_asset_existence`'s owner_mismatch check can be backed by live data there. Every non-PE country is still mock-only until each one's real contract is likewise confirmed rather than guessed.

## 2026-08-26 — Colombia, Chile, Argentina, Brazil go live too (Mexico, Ecuador stay mocked)

User asked to make the other countries live too. Started the same way as Peru — searched/fetched Verifik's public docs per country — but hit a real, concrete demonstration of exactly the risk this whole feature had been guarding against: two independently-fetched sources for **Mexico** flatly contradicted each other. One fabricated a complete, plausible-looking response with a `"owner": "Carlos Rodríguez"` field; a second, differently-rendered source explicitly said no such field exists and listed a different field set entirely (`licenseEntity`, `assemblyPlant`, `cylinderNumber`...). A repeated WebSearch echoed the same fabricated owner example nearly verbatim, suggesting confabulation rather than genuinely scraped content. Conclusion: AI-summarized documentation fetches are not trustworthy enough on their own for this — the project's own stated discipline ("guessing would ship a broken integration") has to extend to *not trusting an LLM's paraphrase of docs* either, not just to not hand-guessing the format myself.

**Fix: stopped trusting doc summaries and called the real API directly** with the user's live token for every candidate endpoint/plate, and used the actual HTTP responses (200s, and even the exact wording of 4xx/5xx errors) as ground truth instead. Results:
- **CO** (confirmed, real 200): real endpoint is `GET /v2/co/runt/vehicle-by-plate-simplified?plate=&documentType=&documentNumber=` (not `fasecolda/values-by-plate`, the original guess) → `data.vehicle.{marca,linea,modelo(=year, a genuine RUNT quirk),color,estadoDelVehiculo,noVin}`. No owner field — confirmed by the real null-valued response body itself, not by a doc claim.
- **CL** (confirmed, real 200 with actual data): `GET /v2/cl/vehicle?plate=` → `data.{plate,mark(not "brand"!),model,year,chasisNumber,owner,color,rut,fines,...}`. This is the **only** confirmed-live country that actually returns an owner name — a live test plate came back with a genuine company name.
- **AR** (confirmed, real 200 twice with two plates): `GET /v2/ar/vehicle?plate=` → `data.{plate,brand,model,year,type,version}`. No owner/color/VIN at all.
- **BR** (confirmed, real 200 after retrying with a Mercosul-format plate — the first, old-format plate got a real but unhelpful `422 ..._does_not_have_modelYear`): `GET /v2/br/vehicle?plate=` → `data.{plate,brand,model,modelYear,color,...}`. The error message itself confirmed `modelYear` as a genuine field name before a success case ever came back. No owner; a `chassis` field mentioned in docs never actually appeared in the one real success observed, so it was deliberately left unmapped rather than guessed back in.
- **MX** (left unconfirmed): the same path pattern as Peru's endpoint (`/v2/mx/vehiculo/placa`) returned a consistent `500 InternalServerError: "501"` across three different test plates — not plate-specific, so either the path is wrong or this Verifik product isn't enabled on the current plan. `VerifikClient._call` still refuses this country.
- **EC** (left unconfirmed): `/v2/ec/vehiculo/placa/multas?plate=` is reachable and auth is accepted (clean structured `404` for two different unregistered plates), but no real success payload was ever observed to confirm the field shape against — and it's a *fines* endpoint, not a general vehicle-info one, so even its documented shape bundles brand+model into one string. Still refuses to guess.

**Changes**: `phase1_ingestion_parsing/vehicle_verify.py` — `COUNTRY_CONFIG` field maps corrected for CO/CL/AR/BR to the real confirmed field names (CO's `estado`→`estadodelvehiculo`, `vin`→`novin`; CL's `marca`→`mark`; etc.), each with an inline comment recording exactly what was tested; `MockVehicleClient._SAMPLES` rewritten for CO/CL/AR/BR to mirror the real response shapes (MX/EC samples left as best-effort, clearly commented as unconfirmed); `VerifikClient._call` generalized from a single PE-only branch to a small `_CONFIRMED_PATHS` table covering PE/CO/CL/AR/BR, with CO's `document_type`/`document_number` extra inputs forwarded as Verifik's actual `documentType`/`documentNumber` camelCase query params; MX/EC still raise `NotImplementedError` with a docstring explaining exactly what was tried.

**Verified**: `tests/test_vehicle_verify.py`'s Colombia test updated (its `owner == "Carlos Gomez"` assertion no longer held now that the mock matches reality — real RUNT has no owner field) plus a new Chile test asserting the real owner behavior; full suite 95/95. Re-ran live calls through `make_client()` for all five confirmed countries with the user's real token: PE/CL/AR/BR returned genuine registry data (including Chile's real owner name), CO correctly returned an all-blank record for a plate/document combo that doesn't exist in the real registry (confirmed against the raw null-valued API response, not a mapping bug). Pushed to the deployed repo.

**Also**: added `clean_plate()` (uppercase, alphanumeric-only) inside both `MockVehicleClient.lookup()` and `VerifikClient.lookup()` — the one shared entry point both call paths (single-plate text input, Excel bulk upload, and the Asset-register live-lookup path) already funnel through, so no caller changes were needed anywhere in `streamlit_app.py`. Motivated by Verifik's own docs consistently requiring a plate "without spaces or points" — a plate typed as "ABC-123" would otherwise reach the live API unmodified. Verified live: `"BB-CC12"` and `"BBCC12"` against Chile's real API returned byte-identical results.

## 2026-08-26 — SOP coverage review, then wired `rank.forensic_route` (was dead code)

Per user request: read the six SOP docs in `Documentation/` (root) and score this build against each one's stated objectives. Full verdict table written to `../PROGRESS_SOP_analysis.md` (root-level, since it compares against a sibling folder, not just this one). Bottom line: SOPs 2/3/4 (collections/disbursements/reported-cash) are hit or mostly hit with real production-file validation; SOP 1 (bi-weekly cash tracking — cut-off sync, FX normalization) has no material implementation; SOPs 5/6 (sample testing, asset existence) have genuine but narrower-than-specified coverage.

Picked the single cleanest gap to close: `phase3_anomaly_reporting/rank.py::forensic_route()` (A3 step 5 / D1 — items at/above the 0.75 `anomaly_score_high` threshold routed to forensic review) was fully implemented and correct but **never called anywhere** — confirmed via grep, only referenced in this file's own gap notes since 19 Aug. `rank_exceptions()` normalizes/sorts severity but doesn't filter; nothing surfaced the forensic subset to a human.

**Changes**:
- `phase3_anomaly_reporting/reporter.py`: `build_report()` gained an optional `thresholds` param. When passed, both outputs gain a forensic section: JSON gets a `forensic_review: {threshold, count, exception_ids}` key; Markdown gets a `## Forensic review queue` section listing the routed items (or an explicit "none" line). Omitting `thresholds` keeps the old shape exactly (no forensic key at all) — a deliberate compatibility choice, not required by any current caller, but cheap and avoids ever emitting a misleading `count: 0` for a caller that never asked.
- `phase3_anomaly_reporting/__init__.py`: exported `forensic_route` alongside the existing three.
- `runner.py` / `app/streamlit_app.py` (both `build_report` call sites: the live pipeline run and `_persist_run`'s reload path): now pass `thresholds=cfg.thresholds` / `CFG.thresholds`.
- `app/streamlit_app.py` Results tab: added a "Forensic review queue" callout (`st.error` + table) directly under the exceptions table, computed via the same `forensic_route()` call — the one already-correct function, not a second implementation.

**Verified**: new `tests/test_reporter.py` (3 tests: forensic key omitted when no thresholds given, high-severity items listed and low-severity ones excluded from both JSON and MD, empty-queue case reports explicitly rather than omitting the section). Full suite 95 → **98 passed**. `py_compile` clean on all four changed files. CLI `runner.py` smoke test against `samples/` (real Absa PDF + loan tape): JSON report's `forensic_review` correctly listed the 3 severity-1.00 reconciliation exceptions (collections/disbursements/cash, all reported=0 since no `--reported` arg was given) with `threshold: 0.75`; MD report rendered the matching section. Streamlit `AppTest` boots with 0 exceptions.

## 2026-09-01 — live browser verification of the deployed workflow, then FX normalization (closes part of the SOP 1 gap)

Per user request ("how can I confirm if everything works" -> "yes" to actually driving the app): launched the real Streamlit app (own instance, port 8611 — an unrelated app was already running on the usual 8502 from a different project, left untouched) and drove it with Playwright (headless Chromium, installed via `npx playwright install chromium` since `chromium-cli` wasn't available on this machine): uploaded `samples/loan_tape_sample.csv` + `samples/statement_absa.pdf` in the Run tab, ran verification, confirmed the Results tab's aggregates/exceptions/forensic-review queue rendered correctly, approved one exception in Review & sign-off (confirmed for real via `verifications.log.jsonl` — a `review`+`feedback` pair actually got appended, not just a UI checkmark), and confirmed the Audit tab's Run log / Lead-time / Last-verification-per-borrower tables render with real data. Zero console errors throughout. Cleaned up the two gitignored `out/<run_id>/` folders the smoke test left behind; left the two synthetic log lines in place (append-only by design).

**Then**, per user's explicit priority pick from a gap-closing menu: built **FX normalization for cash tracking** — the concrete, no-external-blocker half of `PROGRESS_SOP_analysis.md`'s "SOP 1 — Not hit" verdict ("aggregates still sum mixed currencies with no FX lookup"). Cadence/cut-off-date sync and dual-control login remain untouched (separate gaps; cut-off sync is a distinct feature, dual-control is a human-process control outside a file-upload tool's reach).

**Design** (assumptions stated to the user before writing code, per this repo's rule 1): a static, config-driven rate table — same discipline as the B3 thresholds — not a live FX API (no provider/key requested). The rates table ships **empty** with no invented numbers (matches `vehicle_verify.py`'s established "refuse to guess an external fact" discipline); a currency present on a row but missing from the table is left unconverted and its code surfaced as a warning, never silently mis-summed. A blank currency (most current single-currency sources) is assumed already-base. Passing no `fx` config keeps `calculate_aggregates()` byte-identical to its pre-FX behavior.

**Changes**:
- `config.yaml`: new `fx: {base_currency: USD, rates: {}}` section, heavily commented on the "never guess a rate" discipline.
- `phase0_foundations/fx.py` (new): `FXConfig` dataclass + `convert_to_base(amount, currency, fx) -> (converted, unmapped_code|None)`.
- `phase0_foundations/config.py`: `Config` gained an `fx: FXConfig` field, parsed the same way as `thresholds`/`llm`.
- `phase2_verification_engine/calculate.py`: `calculate_aggregates(rows, fx=None)` — when `fx` is passed, converts each row's `amount` via its `currency` field before summing; adds `fx_base_currency`/`currencies_seen`/`unmapped_currencies` (the last only when non-empty) to the returned dict. `fx=None` (the default) is unchanged from before.
- `runner.py`: `calculate_aggregates(all_rows, fx=cfg.fx)`.
- `app/streamlit_app.py`: all four `calculate_aggregates` call sites now pass `fx=CFG.fx`. Also fixed the **other** mixed-currency bug this surfaced: `sum(bank_balances)` (the calculated-cash-total path, entirely separate from `calculate_aggregates`) summed raw closing balances with no currency awareness at all — added `_statement_currency()` (prefers the PDF's `shape`-detected currency, falls back to a row's own) and converts each balance before summing. Unmapped currencies from any of the three aggregates or the balance path are merged into `run.inputs["unmapped_currencies"]` and surfaced as an `st.warning` in the Results tab.
- `phase3_anomaly_reporting/reporter.py`: new `## Currency` section in the Markdown report when `unmapped_currencies` is present (JSON gets it for free via the existing `inputs`/`aggregates` passthrough).
- `phase0_foundations/__init__.py`: exported `FXConfig`/`convert_to_base`.

**A real gap found and fixed while verifying this end-to-end** (not guessed, found by actually running it): the CLI smoke test against the real Absa PDF showed `currencies_seen: ['KES']` but supplying a real `KES: 0.0067` rate barely moved the total (4,676,240 -> 4,672,813, not the ~150x drop a real conversion implies). Root cause: `extract.py`'s per-line PDF parsers (`parse_statement_text`, `_scan_numbered_table`, the OCR path) always hardcode `"currency": ""` on every row — only `detect_shape()` reads a currency off the statement's header/metadata text, once per file, and that never propagated down to the transaction rows `calculate_aggregates` actually reads. Only the loan-tape CSV's own `currency` column (which real sample happens to have) was converting; every PDF-sourced bank-statement row — the actual multi-currency case SOP 1 is about — silently stayed unconverted. Fixed with `_stamp_currency(rows, shape)`, called at all three `extract_pdf()` return sites (numbered-table, flat-text, OCR): fills a row's blank `currency` from `shape["currency"]`, never overwrites one already set. Re-ran the same live test after the fix: `KES: 0.0067` now correctly collapses `cash_in` from 4,672,813 to 31,330 (a genuine ~150x conversion).

**Verified**: new `tests/test_fx.py` (6 tests: default/blank/base/known/unknown-currency conversion), `tests/test_calculate.py` +4 (no-fx unchanged, known-currency conversion, unmapped-currency flagged not crashed, blank-currency assumed-base), `tests/test_extract.py` +4 (`_stamp_currency` fills/doesn't-overwrite/no-ops, and a real-Absa-sample integration test asserting every extracted row now carries `currency: "KES"`). Full suite 98 → **112 passed**. `py_compile` clean on all eight changed/new files. Two live CLI runs against the real Absa PDF + loan tape: empty rates table reproduces the exact pre-FX totals plus a `KES` unmapped-currency warning (proves backward compatibility); a `KES: 0.0067` rate correctly converts both the tape rows and, after the `_stamp_currency` fix, the PDF bank-statement rows. Streamlit `AppTest` still boots with 0 exceptions. Deleted the temporary comparison config files and smoke-test `out/` folders (gitignored, never committed).

**Not done**: cut-off-date synchronization (ensuring all uploaded statements/ledger are as-of the same date before comparing) and dual-control login enforcement — the other two named pieces of SOP 1's "Not hit" verdict — remain open, by the user's explicit scope choice.

## 2026-09-01 — live FX-rate lookup (fawazahmed0/currency-api), replacing the empty static table's "someone must hand-enter rates" gap

Per user request to integrate an open-source API for FX rates, rather than leaving the static `fx.rates` table in config.yaml permanently empty.

**Picked, then verified live before wiring in** (same discipline as `vehicle_verify.py`'s confirmed-only country contracts — call the real thing, don't trust a doc summary): the obvious first candidate, the ECB-based Frankfurter API, was tested live and found to **not cover KES, NGN, GHS, or RWF at all** (ECB simply doesn't publish reference rates for these) — i.e. it would be useless for the actual currencies this business needs, discovered by calling it, not by reading about it. Tested `fawazahmed0/currency-api` (MIT-licensed, open source, GitHub-hosted, served free with no API key from two independent CDN mirrors — jsdelivr and a Cloudflare Pages domain) instead: confirmed live that it has KES/NGN/GHS/RWF and returns real current rates.

**Design**: live rates are fetched at run time and take priority over `config.yaml`'s static table on overlap (more current); a currency neither the live source nor the static table has is still left unconverted and surfaced via the existing `unmapped_currencies` warning — never guessed. Skips the network call entirely when no non-base currency is present in the run's data. Two independent mirrors, tried in order, so one CDN being down doesn't break it.

**Changes**:
- `phase1_ingestion_parsing/fx_rates.py` (new): `fetch_live_rates(base_currency, currencies, timeout=10) -> (rates, as_of_date)`, `urllib.request`-based (no new dependency, matching `VerifikClient`'s style), raises `FXFetchError` only if both mirrors fail.
- `app/streamlit_app.py`: new checkbox in the Run tab, "Fetch live FX rates..." — **default ON** (opt-out), since an interactive UI can show the fetch result/failure before anyone trusts the numbers, same posture as the existing default-live Verifik registry lookup. `_run_pipeline` gained a `use_live_fx` param; computes `effective_fx` (live rates merged over `CFG.fx`) once, used by all four `calculate_aggregates`/balance-conversion call sites. Surfaces a `live_fx_status` caption ("Live FX rates fetched for KES (as of 2026-09-01).") in the Results tab.
- `runner.py`: new `--live-fx` flag — **default OFF** (opt-in), so a plain CLI run stays reproducible given the same input files, matching the CLI's existing fully-offline posture (it never calls Verifik either). Same `effective_fx` logic, printed to stdout as `FX: ...`.

**Verified**: new `tests/test_fx_rates.py` (4 tests, HTTP mocked — inversion math, an unmapped currency is omitted not guessed, second-mirror fallback on first-mirror failure, `FXFetchError` when both fail). Full suite 112 → **116 passed**. Real live CLI runs against the real Absa PDF: `--live-fx` fetched a genuine current KES rate and correctly collapsed `cash_in` to a plausible USD figure (~36,105, matching the live ~129.5 KES/USD rate against the known KES total); without the flag, behavior is byte-identical to before (still flags `unmapped_currencies: ['KES']`) — confirms the opt-in gating actually works. Live browser pass (Playwright, own Streamlit instance on a scratch port): checkbox defaults checked, run produces the exact same converted figures and the "FX: Live FX rates fetched for KES (as of 2026-09-01)." caption renders in the Results tab. Cleaned up the scratch config files, `out/` smoke-test folders, and the browser-driven run's log lines' output folder (gitignored; the six log lines from these runs were left in place, same append-only-log stance as the 2026-09-01 entry above).

## 2026-09-01 — real OCR accuracy testing (per user request), found and fixed a real preprocessing bug

Per user request to "test the OCR functionality" — previously only ever tested via mocks (`tests/test_ocr.py`, `test_extract.py`'s OCR tests all patch `ocr_extract_text`'s return value), since `tesseract_available()` had never returned `True` on this machine. Asked the user first: real accuracy testing needs the `tesseract` binary, and installing it writes outside `verifications-automation/` (this repo's own AGENTS.md rule 7) — user approved. Installed via `winget install --id UB-Mannheim.TesseractOCR` (system-wide, standard Windows location, auto-detected by `ocr.py`'s existing `_WINDOWS_TESSERACT_PATHS` fallback — no `TESSERACT_CMD` needed).

**Method**: built two test scans (a scratch script, deleted after use) rather than trusting the mocked tests — (1) rendered the real, tie-out-verified `samples/statement_absa.pdf` to page images and rebuilt an image-only PDF from them (a genuine simulated scan of real, known-correct data: 15 transactions, ins=4,672,790.16, outs=4,015.00, closing=2,003.94, currency=KES); (2) a synthetic short statement (4 transactions) with a large blank area below the text, to test a sparse-page shape.

**Finding 1 — real, structural gap**: OCR genuinely runs and reads real text — `detect_shape()` correctly recovered `numbered_table` layout, bank `absa`, account number, and currency `KES` straight from the OCR'd text — but **zero of the 15 real transactions ever came through**. Root cause: `extract_pdf()`'s OCR path only ever feeds OCR'd text into `parse_statement_text()` (the flat-text regex parser for `two_date`/`single_date`/`iso_running` layouts); the `numbered_table` layout — the one this project's own real validated sample uses, and the most structurally complex — is only ever parsed by `_scan_numbered_table()`, which depends on `pdfplumber`'s per-word (x, y) coordinates that plain OCR text extraction (`pytesseract.image_to_string`) discards entirely. So a scanned numbered-table statement always falls through to the "manual transcription required" placeholder today, correctly-but-uselessly (no wrong numbers, just no numbers). **Not fixed** — recovering this would mean feeding `pytesseract.image_to_data`'s word-level bounding boxes into (or building a second version of) `_scan_numbered_table()`'s coordinate logic; flagged as a real, scoped gap rather than attempted inline.

**Finding 2 — real, reproducible bug, fixed**: on the synthetic sparse-page test, OCR through the *existing* preprocessing pipeline (`grayscale` -> `Contrast(1.5)` -> `Sharpness(2.0)`) recovered only 1 of 4 transaction lines — silently truncated, no error. Isolated step-by-step: grayscale alone and grayscale+contrast alone both recovered all 4 lines cleanly (matching raw, unprocessed OCR); adding `Sharpness(2.0)` — alone or stacked after contrast — is what broke it (stacked was worse: full truncation vs. sharpness-alone's severe scrambling). Cross-checked against the real Absa scan too: the same sharpen step lost ~20% of recoverable text on 2 of 3 real pages, never an improvement anywhere it was tested — directly contradicting the code's own docstring claim that it "measurably improves Tesseract accuracy" (that claim was carried over from the ported source, never independently verified in this codebase until now).

**Fix**: `phase1_ingestion_parsing/ocr.py::_preprocess_image()` — removed the `Sharpness(2.0)` step; kept grayscale + `Contrast(1.5)` (that combination never reproduced either failure and tracks within 1-3% of raw on the real sample). Re-verified after the fix: the synthetic sparse-page test now recovers all 4 rows through the full `extract_pdf()` pipeline (one real OCR digit misread, "29" instead of "25" in a date — a genuine Tesseract character error, correctly caught by the existing sub-0.6 OCR confidence cap rather than a code bug); the real Absa scan's OCR text length improved slightly too (2398 -> 2585 chars) though it still (correctly, per Finding 1) produces zero parsed rows.

**Verified**: new `tests/test_ocr.py::test_preprocess_image_does_not_truncate_a_sparse_page` — a real regression test against the actual Tesseract binary (skips gracefully via the file's existing `tesseract_available()` guard when one isn't present, so it's inert in an environment without the binary rather than failing). Full suite 116 -> **117 passed**. Deleted the scratch PDFs/images/scripts used for this investigation (none committed, none belong in the repo).

**Not done**: the `numbered_table`-via-OCR gap (Finding 1) — a real capability gap, not a bug, and a materially bigger piece of work (coordinate-aware OCR parsing) than this session's scope. Flagged for a separate decision.

## 2026-09-01 — Transaction Matching tab silently mis-reported a schema mismatch as a real result, fixed

Per user request, ran real transaction matching using two files from `Test docs/`: `bank_transactions_with_codes.xlsx` (an "Apex Sterling Bank - Customer Transaction Ledger" export — Date/Transaction Code/Description/Category/Debit/Credit/Running Balance) and `bank_statement_with_codes.pdf` (the same bank's PDF statement, same 25 transactions, same transaction codes — a matched test pair, not a tape-vs-bank scenario).

**Bug found through actual use, not inspection**: uploaded the xlsx as "Reported source" and the PDF as "Independent source" in the Transaction Matching tab and got `0 matched / 0 in reported only / 25 in independent only` — a result that reads as "the reported file has zero transactions in it," which is false. Root cause: the tab hardcoded `reported_rows = load_and_normalize([reported_path], sheet=SHEET_TAPE)` regardless of the uploaded file's actual shape. This xlsx is bank-statement-shaped, not loan-tape-shaped (confirmed directly: `load_and_normalize(path, sheet=SHEET_TAPE)` -> 0 rows; `sheet=SHEET_BANK` -> 25 rows, correctly parsed). A shape mismatch parses to zero rows with no error — `load_and_normalize` doesn't raise on an unrecognized schema, it just finds nothing to map — so the UI rendered a confident-looking wrong answer instead of surfacing the actual problem.

Verified the real, correct answer directly against the underlying engine (bypassing the UI) before touching any code: both files loaded as `SHEET_BANK` -> 25 rows each -> `match_transactions()` -> **25 matched, 0 unmatched either side**, every one a "partial match" (the PDF's description appends the category, e.g. "Direct Deposit / Payroll" vs "Direct Deposit / Payroll Salary"), amounts tying out exactly on every row. Wrote the result to `Test docs/transaction_match_report.xlsx`.

**Fix** (`app/streamlit_app.py`, `tab_match`):
- Added a `st.radio` "Reported source shape" (Loan tape / ledger [default, preserves old behavior] | Bank / mobile statement) next to the reported-source uploader, and a symmetric "Independent source shape" radio for the independent side — shown only for non-PDF uploads, since a PDF always goes through `extract_pdf()` regardless. Reworded both uploader labels and the reported-source caption to make clear this tab isn't limited to a tape-vs-bank pairing — two independent bank/mobile exports of the same account is a legitimate use, picking "Bank / mobile statement" for both sides.
- Whichever shape is picked now selects `SHEET_TAPE` vs `SHEET_BANK` for `load_and_normalize`, on both sides.
- Added the safety net regardless of shape choice: if either side parses to 0 rows, `st.warning()` names the file and the shape that produced nothing and tells the user to try the other one — so a wrong shape pick now surfaces as an explicit warning next to a metrics table that's still technically accurate (0 rows really did parse), rather than a silent, misleading-by-omission result.

**Verified**: `py_compile` clean, full suite still **117 passed** (no existing test touched this tab's internals). Live browser test (Playwright) against the running app, real files: (1) reported-shape left at its old default (Loan tape/ledger) reproduces the original 0/0/25 result but now **with** the new warning naming the exact mismatch — confirms the safety net fires; (2) reported-shape switched to "Bank / mobile statement" -> **25/0/0**, matching the direct-engine result exactly, table and download button render correctly, zero console errors. Cleaned up the gitignored `out/_match_uploads/` folder and scratch driver scripts/screenshots (nothing committed).

## 2026-09-01 — Transaction Matching tab redesigned around a column picker, replacing the shape radios above

Per user request to look at how `github.com/nephy-edge/vehicle-verification` (a live-deployed sibling app, same author, at vehicle-verification.streamlit.app) handles bank-statement extraction/matching, then bring the same UX here: a preview of each file's own real columns, plus a dropdown to choose which column to match on — rather than the shape-radio approach from the entry above.

**What the reference repo does differently** (cloned briefly to `.scratch_vv_clone/` for inspection, deleted after): its `reconciliation.py` never normalizes into a canonical schema at all — it reads Excel/PDF as raw columns and lets the user pick the match column from a live dropdown, which is *why* it never hits the schema-assumption bug fixed in the entry above: there's no schema to violate. Its `match_records()` already supported `case_sensitive`/`ignore_spaces`/`ignore_special_chars` options our `match_transactions()` also already had (ported 2026-08-25) but the UI never exposed. Its PDF extraction is a genuinely more thorough 3-stage fallback than ours: `pdfplumber.extract_tables()` (real table structure) -> OCR'd text matched against a hardcoded transaction-line regex -> OCR converted to a *searchable PDF* (`pytesseract.image_to_pdf_or_hocr`, which preserves word positions) and re-plumbered — that third stage is a real, usable technique for closing the `numbered_table`-via-OCR gap flagged 2026-09-01 above, not implemented here yet. Also confirmed: its `_preprocess_image` has the **identical** `Contrast(1.5)` + `Sharpness(2.0)` bug fixed in today's earlier OCR entry — inherited from the same common ancestor, never caught there either.

**What was built here** (adapted, not copied — reusing this codebase's own validated pieces rather than porting the reference's raw parser):
- `phase1_ingestion_parsing/ingest.py`: new `read_raw_table(path)` — a public wrapper around the already-tested `_read_tabular`/`_read_excel` (which already auto-detects a real header row past a title/spacer row), for callers that want a file's actual column names instead of the canonical tape/bank/mobile/ledger schema.
- `phase1_ingestion_parsing/extract.py`: new `extract_pdf_table_rows(path)` — tries `pdfplumber.extract_tables()` first (reusing `ingest.py`'s `_looks_like_header` keyword check to find the real header row among a page's tables, concatenating across pages that share it), falling back to the existing validated `extract_pdf()` canonical rows (relabeled to generic Date/Description/Amount/Direction/Currency columns) when no real embedded table is found — reuses proven parsing for that case rather than a second raw one.
- `phase2_verification_engine/transaction_match.py`: new `build_generic_match_report()` — flattens a match result keeping *every* column from both sides (prefixed `Reported: `/`Independent: `), for matching on arbitrary raw columns where there's no fixed description/amount pair to fall back on. `match_transactions()` itself needed no changes — it already accepted `reported_field`/`independent_field`/normalization options from the 2026-08-25 port; the UI just never exposed them.
- `app/streamlit_app.py`, `tab_match`: replaced the shape-radio UI from the entry above with a preview + column-picker, closely mirroring the reference app: after both files upload, shows each side's real extracted columns and a 5-row preview (`st.dataframe(...head())`), two `st.selectbox`es to pick the match column per side (populated from each side's actual columns), and a "Matching options" expander exposing case-sensitivity/ignore-spaces/ignore-special-chars/partial-match toggles. Still warns (rather than silently showing a misleading result) if either side extracts to 0 rows.

**A real bug found and fixed while building this** (found by actually running it against the real files, not assumed): the first version of `extract_pdf_table_rows` picked pdfplumber's `"Starting Balance $X | Ending Balance $Y"` account-summary table as *the* header, because it loosely satisfied the keyword check too (the word "balance" appears in it) — every real transaction row that followed then got force-mapped onto those four wrong, unrelated column names. Fixed by requiring a candidate header table to have at least one data row beneath it (`len(table) >= 2`) before accepting it — a one-row summary table never has that, so it's now correctly skipped in favor of the real 7-column transaction table on the next page.

**Verified**: new tests — `tests/test_ingest.py` (`read_raw_table` keeps a file's own column names past a title row), `tests/test_extract.py` (+3: the summary-table-vs-real-header bug fixed above, a repeated header on a later page is dropped not double-counted, the canonical-fallback path relabels columns correctly — all via mocked `pdfplumber.open`, no real PDF fixture needed), `tests/test_transaction_match.py` (+3: matching on an arbitrary field name, `build_generic_match_report`'s per-side column prefixing, that it keeps every column rather than a fixed pair). Full suite 117 -> **124 passed**. Direct-engine test against the real files from `Test docs/`: matching on "Transaction Code" vs "TRANSACTION CODE" now gives **25 matched (all exact), 0/0 unmatched** — an improvement over the previous entry's result (25/0/0 too, but via canonical `description` matching, which produced 25 *partial* matches since the PDF's description differs slightly from the Excel's). Live browser test (Playwright) confirms the full flow end-to-end: preview panels show each file's real columns (`Transaction Code`/`TRANSACTION CODE` visible and selectable), matching on them gives the same 25/0/0 exact-match result live, generic report table renders with every original column prefixed by side, zero console errors. Cleaned up the reference-repo clone, gitignored `out/_match_uploads/`, and scratch driver scripts/screenshots (nothing committed).

## 2026-09-01 — Transaction Matching tab: per-column totals check

Per user request ("aggregations on the amount columns to see totals for each and if the totals match"), added a standalone totals comparison between the preview and the row-level column picker in `tab_match` — deliberately independent of whether individual rows match, since a real statement often splits debit/credit into separate columns (this session's own `Test docs/` files do) rather than one signed amount column.

**Changes**: `phase2_verification_engine/transaction_match.py` — new `parse_amount(value)` (tolerant of `$1,234.56`, `(200.00)` parenthesised negatives, blank/`N/A` cells -> `None`, native `int`/`float`/`NaN`) and `sum_amount_column(records, column) -> (total, parsed_count, skipped_count)`, the count split so a caller can flag "3 of 25 rows weren't numeric" rather than silently folding them into the total as zero. `app/streamlit_app.py`: two new selectboxes (amount column per side, auto-guessing a column with "amount" in its name, else defaulting to "(none)"), three `st.metric`s (reported total / independent total / difference), and a green "Totals match" / amber "don't match" banner at a 0.1% tolerance (float noise only, not a real materiality threshold — this is a quick sanity check, not `reconcile.py`'s variance test).

**Verified**: 6 new tests in `tests/test_transaction_match.py` (currency-symbol/comma parsing, parenthesised negatives, native numbers, blank/unparseable -> `None`, NaN excluded, sum-with-skips). Full suite 124 -> **130 passed**. Live browser test against the real `Test docs/` files (own Streamlit instance, port 8503): picking "Credit (+)" vs "DEPOSITS (+)" gives **12,466.98 = 12,466.98, difference 0.00, "Totals match."**, and correctly reports 16 of 25 rows skipped on each side (the debit-only rows, blank in a credit column) — confirms both the parsing and the skip-accounting are correct on real data, not just synthetic test cases.

## 2026-09-01 — Deployed repo sync + Streamlit Cloud stale-import gotcha

Pushed today's accumulated work (FX normalization, live FX rates, OCR sharpening fix, Transaction Matching redesign, column totals check) to `github.com/nephy-edge/verifications-automation` (this project's actual deployed repo — distinct from the `verifications-automation-v2` fresh-history repo created earlier, and distinct from the `nephy-edge/projects` monorepo this working copy physically lives inside, which carries unrelated dirty state from other projects and can't be pushed from directly). Method: shallow-cloned the target repo, `robocopy`'d only this folder's contents into it (excluding `.venv`/`__pycache__`/`samples/`/`out/`/`.env`/logs, matching its own `.gitignore`), reviewed the real diff (18 files, +998/-48, plus 5 new files never previously pushed), confirmed no secrets, committed, pushed to `master` (its default branch). `robocopy` and its `/E`/`/XD` flags needed `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"` in front — Git Bash's path-conversion otherwise mangles a bare `/E` flag into a phantom drive path.

**Gotcha hit on the Streamlit Community Cloud deploy** (worth remembering — hit locally too, with our own dev server, earlier this session): its "Pulling code changes from GitHub" step does a `git pull` + script rerun without restarting the Python process, so an already-imported submodule (e.g. `phase1_ingestion_parsing.extract`) stays cached in `sys.modules` at its *pre-pull* version even though `app/streamlit_app.py` itself reruns against the new code — surfaced as `ImportError: cannot import name 'extract_pdf_table_rows'` even though the pushed file on GitHub genuinely had it (confirmed via `raw.githubusercontent.com`). Fix is a full **Reboot app** from the Streamlit Cloud dashboard (fresh process, fresh imports) — no code change, and not something triggerable via CLI/API.

## 2026-09-01 — Scanned-statement gap: OCR now recovers the numbered-table layout too, via a searchable-PDF intermediate

Per user question ("for scanned images, how do we go about that"), explained the current split behavior (Run tab already OCRs a scanned PDF via `ocr_extract_text`, but only for the 3 flat-text layouts; the `numbered_table` layout — this project's own real Absa sample's layout — had a measured 0% recovery rate from a scan, logged in the OCR-accuracy-testing entry above; the Transaction Matching tab's raw-column picker degrades further, falling back to 5 generic columns on any scan since `pdfplumber.extract_tables()` needs ruling lines a scan doesn't have; and no uploader anywhere accepts a bare image file, only PDF/CSV/XLSX). User chose to close the `numbered_table` gap — the fix found but not implemented while reading `vehicle-verification`'s 3-stage PDF fallback (OCR -> searchable PDF -> re-plumber).

**Root cause, precisely**: `_scan_numbered_table()` (the `# DATE NARRATION DEBIT CREDIT BALANCE` column mapper) needs each word's x-position on the page to know which column it belongs to; `ocr_extract_text()` returns a flat string with no positions, so that layout could never be recovered from a scan no matter how accurate the OCR was.

**Fix**: `phase1_ingestion_parsing/ocr.py` — new `ocr_to_searchable_pdf(pdf_bytes) -> bytes | None`, using `pytesseract.image_to_pdf_or_hocr(..., extension="pdf")` per rendered page (Tesseract's own mode for writing an invisible, *positioned* text layer) merged across pages with `pypdf.PdfWriter`/`PdfReader` (a dependency requirements.txt already declared but nothing in the codebase actually used yet). `phase1_ingestion_parsing/extract.py` — new `_extract_numbered_table_from_ocr()`, tried in `extract_pdf()` before the existing flat-text `_extract_from_ocr()`: writes the searchable PDF to a temp file, runs the existing, unmodified `_scan_numbered_table()` against it (via `pdfplumber`, which can now read real word coordinates out of Tesseract's output the same as it would a native text layer), stamps currency/confidence/`ocr=True` exactly like the flat-text path. Runs its own independent OCR pass rather than sharing one with `_extract_from_ocr` (double Tesseract cost on a genuine scan, accepted deliberately) to avoid touching the well-tested flat-text path at all — this only adds a path, doesn't rewire an existing one. Does **not** help `extract_pdf_table_rows()`'s ruling-line table detection (still needs vector graphics a scan doesn't have) — a scan fed to the Transaction Matching tab now recovers real transaction *rows* via the canonical fallback it already had, but still only the 5 generic columns, not the file's real ones.

**Verified, honestly**: real end-to-end test (`tests/test_extract.py::test_numbered_table_layout_via_ocr_on_a_real_synthetic_scan`, guarded by `tesseract_available()`) — rendered the real, tie-out-verified `samples/statement_absa.pdf` to page images (stripping the text layer, a genuine simulated scan) and ran it through `extract_pdf()`. Recovered **3 of the file's 15 real transactions**, each one checked against the native-text-layer parse for an exact match (date, amount, direction, description all correct) — real, correctly-parsed data, not noise. This is **0% -> ~20% recovery, a real and measured improvement, not a full fix**: many numbered rows still fail the parser's row-number regex after an OCR misread of the leading `#` token, and are silently dropped rather than counted, same as any other row that doesn't match a known pattern. Also added 2 mocked unit tests (searchable-PDF path tried and used when it finds rows; falls through to the flat-text path unchanged when it doesn't) and 3 new `tests/test_ocr.py` tests for `ocr_to_searchable_pdf` itself (garbage/empty bytes -> `None`; a real Tesseract round-trip confirms `pdfplumber` can read positioned words back out of what Tesseract writes). Full suite 130 -> **136 passed**. `requirements.txt` comment updated now that `pypdf` is actually used. Pushed to `nephy-edge/verifications-automation` (`9b5e4ff`), same shallow-clone-and-sync method as the previous push.

## 2026-09-01 — Raw image uploads (the other option from the gap above), and a real limit found while verifying it

Per user report ("if i upload the image statement, nothing happens") — the real cause: every `st.file_uploader` in the app restricts `type=` to `pdf`/`csv`/`xlsx`/`xls`, so Streamlit's own file picker rejects a `.png`/`.jpg` silently (no visible crash, easy to miss) before any of our code ever runs. This was the "accept raw image uploads" option flagged but not picked in the entry above.

**Fix**: `app/streamlit_app.py::_save_upload()` now converts a raw image (`.png`/`.jpg`/`.jpeg`/`.tif`/`.tiff`/`.bmp`) into a single-page PDF at save time (Pillow, already a dependency), before anything else touches it. Every downstream PDF-only code path (`extract_pdf`, its new searchable-PDF OCR fallback, `extract_pdf_table_rows`) then handles it exactly like any other scanned PDF, with zero separate image-handling logic anywhere else. Extended the `type=` list on the two uploaders that already accepted `pdf` (Run tab's "Bank statements", Transaction Matching's "Independent source") to also accept image extensions; left uploaders that never accepted PDF either (Mobile money statements, loan tape, ledger, assets) untouched — out of scope, a different, unrequested expansion.

**A real limit found while verifying this, not a bug**: uploaded each of the real Absa sample's 3 pages as a standalone image, one at a time, through the actual running app. None of the 3 individually recovered a real transaction — each correctly landed on the existing "needs manual spot-check" placeholder rather than a wrong answer, but not real data either, even though the *full 3-page document* (verified in the entry above) recovers some. Traced why directly: `_scan_numbered_table()` never resets its `header_columns` between pages — once it finds a clean header on any page, that mapping carries forward for every later page even if a later page's own repeated header OCRs poorly. A full multi-page scan benefits from whichever page's header OCR'd best; a single cropped page has no such fallback and lives or dies on that one page's own OCR fidelity for the header line specifically. Confirmed directly (bypassing the app): page 2 in isolation OCR'd its header line correctly (`_table_header_columns` found it) but every row line was missing its leading row-number token (an OCR dropout, not a header problem), so zero rows matched `_ROW_NUM_RE`; page 3 in isolation didn't even OCR its own header line cleanly this time (non-deterministic across OCR runs) and found nothing at all. Not fixed — flagged as a real, structural consequence of the existing coordinate-based design, not something to patch reactively per failure mode.

**Verified**: full suite still **136 passed** (no test changes needed — `_save_upload` is UI glue tested live, per this project's existing convention of not unit-testing `streamlit_app.py`'s helpers). Live browser test, own Streamlit instance: uploading a real PNG (rendered from `samples/statement_absa.pdf`) to the Run tab's Bank statements uploader now accepts the file, runs the full pipeline to completion, and either recovers real rows or lands on the existing, clearly-worded placeholder — never silently does nothing, which was the actual bug reported. Cleaned up scratch test images/PDFs and the `out/` run-artifact folder (gitignored, nothing committed). Not yet pushed.

## 2026-09-02 — Merged Transaction Matching into the Run tab

Per user question ("do the run verification and transaction matching pages do the same thing") — answered no (Run verification is the required aggregate reconciliation that feeds the audit trail/sign-off; Transaction Matching is an optional, on-demand row-level drill-down that doesn't touch the audit trail) — then per follow-up request ("can i merge the two to one page"), asked which kind of merge: a plain visual combine (same two features, same separate uploads, just one page) or a deeper integration reusing the Run tab's already-uploaded files for matching automatically. User picked the plain combine.

**Change**: `app/streamlit_app.py` — dropped `tab_match` from the `st.tabs([...])` call (5 tabs -> 4) and re-scoped its entire block from `with tab_match:` to a second `with tab_run:` block (Streamlit containers accept being reopened; content just appends in execution order) with a `st.divider()` ahead of it. No logic changed — same two uploaders, same preview/column-picker/totals/matching flow, just living inside "Run verification" below the Results section instead of its own tab.

**Verified**: full suite still **136 passed** (no test touches `tab_match`'s internals). Live browser check on the running app: exactly 4 tabs now (`Run verification`, `Asset & vehicle verification`, `Review & sign-off`, `Audit log & hardening`), and scrolling down "Run verification" shows Upload -> Run -> Results -> a divider -> "Detailed transaction matching" in one continuous page. Not yet pushed.

## 2026-09-02 — Transaction matching now reuses "1. Upload sources" instead of asking for files twice

Per follow-up request ("have the second part replace the first one, but retain the option to get the loan tapes from redshift and use ledgers") — clarified scope first (asked whether "replace" meant removing the aggregate Run/Results flow entirely, or just removing transaction matching's own duplicate uploaders in favor of reusing what's already uploaded above). User picked the latter: keep the aggregate reconciliation flow (it's what feeds the audit trail/sign-off), just stop asking for the same files twice.

**Change**: `app/streamlit_app.py` — removed the "Reported source"/"Independent source" `st.file_uploader`s from the transaction-matching section. In their place, two `st.selectbox`es built from candidates already available higher up the same page: reported = each uploaded loan-tape file, the Redshift-fetched tape (`loaded_tape_records`, labeled with the borrower name) if loaded, and each uploaded ledger file; independent = each uploaded bank statement and mobile-money file. New `_load_match_candidate(kind, payload)` resolves either a `("redshift", records)` pair (already in memory, no file to read) or a `("file", UploadedFile)` pair (saved via the existing `_save_upload` — image-to-PDF conversion included — then read via `read_raw_table`/`extract_pdf_table_rows` exactly as before) into `(records, columns, preview_df)`. Everything downstream (preview, column totals, column-picker matching, results) is unchanged — same variable names, same functions, just fed from the resolved candidate instead of a second upload.

**Verified**: full suite still **136 passed** (no test exercises this UI-glue selection logic directly, consistent with this project's existing convention for `streamlit_app.py`). Live browser test against the real `Test docs/` files: uploaded `bank_transactions_with_codes.xlsx` as the Run tab's loan tape and `bank_statement_with_codes.pdf` as its bank statement (no separate matching upload); the matching section's dropdowns picked them up automatically (only one candidate per side, so each auto-selected); preview showed the correct real columns immediately; matching on "Transaction Code" vs "TRANSACTION CODE" gave the same correct **25 matched, 0/0 unmatched** result as before the refactor — confirms the reuse path produces identical results to the old duplicate-upload path. Not yet pushed.

## 2026-09-08 — SOP 1 / Option A: loan-tape change-detection watcher (cash_watcher.py)

Per user request ("do option a"), built the change-detection watcher that polls
the Redshift Query API for each borrower's loan tape and auto-runs the
verification pipeline whenever the tape changes, so reported/derived balances
update near-instantly after a borrower loads a new tape (vs. the current
manual "next time someone clicks Run" behavior).

**What was added**:
- erifications-automation/cash_watcher.py — a headless CLI watcher. Detects
  change via a stable content watermark per borrower (row count + sorted-SHA-256
  over canonicalized rows, order-insensitive so Spectrum's non-deterministic scan
  order doesn't cause false re-runs). On change, fetches the tape and runs the
  same deterministic pipeline the Streamlit dropdown uses (normalize to events,
  live-FX-normalized aggregates, reconcile, anomaly, rank) and writes a working
  paper + audit-log entry. CLI: --once (poll once), --interval N, --dry-run
  (detect only), --borrower X (restrict), --config. Loop mode polls forever.
- phase0_foundations/config.py — new WatcherConfig dataclass wired into
  Config.sop1 (interval, state file, fetch limit, borrowers, cut-off guard,
  reported totals).
- config.yaml — new sop1.watcher block, data-driven (enabled master switch
  default false, interval 600s, state_file: out/cash_watcher_state.json,
  	ape_fetch_limit: 1000, borrowers list, cut_off_date/cut_off_mode
  hard_stop|backdate, reported totals).
- 	ests/test_cash_watcher.py — watermark stability/order-insensitivity,
  state round-trip, change detection (new/unchanged/re-added), fetch-error
  resilience (per-borrower and borrower-list), cut-off guard, config parsing.
  9 tests.

**Design / scope notes**:
- Read-only by construction: only SELECTs via the edshift-api gateway (which
  enforces SELECT-only + schema allowlist + row caps server-side). No DML/DDL.
- Cut-off guard: if a detected change's latest egin_date is after
  cut_off_date, hard_stop refuses to run (flags it), ackdate runs but
  marks the run post_cut_off in the state file. Default ackdate, disabled
  until a cut-off date is set.
- Default eported derivation mirrors the Streamlit dropdown path (collections/
  disbursements come from the tape itself, so no false reconciliation variance).
- Transient API outages (per-borrower or the /borrowers list) degrade to "no
  changes this poll" rather than killing the long-running loop.
- Deliberately not over-built (do-not-rebuild): no ML, no PDF/statement handling
  here — it watches the tape and runs the existing engine. It does NOT yet
  auto-pull bank statements via Bank Access/ open-banking (separate
  integration), so unreconciled cash remains best-effort until that's wired.

**Verified**: full suite still green (207 passed, no regressions). ruff clean on
cash_watcher.py, config.py, 	est_cash_watcher.py. CLI --once --dry-run
confirmed to not crash on unset REDSHIFT_API_KEY (reports and exits 0). Not
yet pushed.

## 2026-09-08 (follow-up) — closed the biggest untested gap in the watcher

User asked "have you tested it out to see if it works" — honest answer was: unit
logic and CLI resilience tested, but the end-to-end auto-run path (_headless_run
fetch -> detect -> run -> working paper) had only been exercised through mocked
network fakes, never with real row processing, because it's fed entirely from a
live API that wasn't reachable. Added 	ests/test_cash_watcher.py::
test_headless_run_produces_working_paper — feeds a synthetic redshift-style
per-loan tape into _headless_run (the exact function the watcher calls after a
detected change) and asserts: run status = done, non-zero collections and
disbursements aggregates, a un_<id>.json working paper written under
out/<borrower>/, and a watcher_run_completed audit-log line. Full suite now
**208 passed**, ruff clean.

Still not covered (needs live infra, out of scope for offline tests): an actual
poll against a running edshift-api with a real key and a real borrower tape,
and confirmation that reconcile/anomaly run on real normalized rows for every
borrower's actual column layout. Those require the API up + REDSHIFT_API_KEY.

## 2026-09-09 — Watcher review fixes (all review findings addressed)

Addressed every finding from the /review uncommitted pass on cash_watcher.py:

1. **Cut-off guard now uses the borrower's real date column** — _latest_begin_date
   (hardcoded egin_date) replaced with _latest_date(rows, column) where
   column is resolved per borrower via loan_tape_columns.resolve(borrower).
   This means payjoy/metafin (which use origination_date/emi_begin_date, per
   config.yaml's existing overrides) are correctly cut-off guarded instead of
   silently always "ok". State now stores latest_date + date_column.
2. **Failed auto-run is retried** — _poll_once now captures the
   VerificationRun; last_run_at/post_cut_off only advance on
   status == "done", and on failure the stored watermark is popped so the next
   poll re-detects and retries instead of silently consuming the change.
3. **Vacuous reconciliation surfaced** — the report's un.inputs now carries
   independent_source_present: false and a econciliation_scope note stating
   the watcher ingested only the tape (no bank/mobile side), so a green report is
   not misread as "the tape ties out to real inflows/outflows".
4. **No more double fetch** — detect_changes now returns the just-fetched rows
   (orrower_rows) so _poll_once runs without a second API round-trip; the
   re-fetch path remains only as a fallback.
5. **Sampled-watermark caveat documented** — module docstring warns the watermark
   covers the fetched window of a non-deterministic Spectrum scan, so the watcher
   is a sampled detector (rare false re-run possible; larger reads for full coverage).

Tests: 	ests/test_cash_watcher.py grew to 13 cases (borrower-variant cut-off,
retry-on-failure, independent-source-absent report content, latest_date helper).
Full suite **211 passed**, ruff clean. CLI --once --dry-run still degrades to
exit 0 on unset REDSHIFT_API_KEY. Not yet pushed.

## 2026-09-09 — Wire Drive statement drop-folder into the watcher (SOP 1 independent side + audit trail)

Per user guidance (“why not have manual statements put in a drive folder and fetched
from there, as we don’t currently have a way of fetching them automatically�), wired
a **Google Drive statement drop-folder** into cash_watcher.py so the watcher’s
previously-vacuous reconciliation now has a real independent bank/mobile side:

**What was added:**
- phase0_foundations/drive_inbox.py — a read-only (drive.readonly) Drive inbox
  reader: InboxFile (id + modifiedTime ingerprint for idempotent ingestion),
  list_new_statements(folder), download_file(). Uses its OWN OAuth token
  (GOOGLE_OAUTH_INBOX_TOKEN_JSON), separate from gsheet_export.py’s drive.file
  scope (which only sees files the app created and cannot list a human-uploaded
  folder). Additive only; the Sheets export feature is untouched.
- erifications-automation/scripts/google_oauth_setup_inbox.py — one-time browser
  auth granting drive.readonly.
- phase0_foundations/config.py — BankStatementsConfig (enabled, inbox folder,
  per-borrower folder overrides) nested under WatcherConfig.
- config.yaml — sop1.watcher.bank_statements block (default disabled).
- .env.example — documents GOOGLE_OAUTH_INBOX_TOKEN_JSON.

**Watcher changes (cash_watcher.py):**
- _headless_run accepts ank_rows; when present, calculated = independent bank
  aggregates vs eported = tape (a real cross-check) and independent_source_present
  flips to True; the report sets ank_statement_count and calculated_* aggregates.
  When absent, it stays explicitly “NOT VERIFIED� (never a false green).
- _fetch_bank_statement_rows(cfg, borrower, state) downloads new inbox files, retains
  the raw bytes under out/<borrower>/statements/ (the audit trail), records ingested
  fingerprints in state so a file is downloaded once, and re-parses retained files on
  retried runs so a failed run reconciles against the same independent data.
- Fixed a real Windows bug surfaced by the new test: the retained filename used the
  raw fingerprint (ISO timestamp with :), which is invalid in Windows filenames —
  now sanitised (e.sub).

**Verification:** kept read-only by construction (only lists/downloads; never
creates/edits/moves Drive content). Watcher tests grew to 16 (drive ingestion +
idempotency, independent-verified reconciliation, retry) + a new test_drive_inbox.py
(fingerprint, config). Full suite **219 passed**, ruff clean. CLI smoke still exit 0.

**Caveats / scope:**
- Requires the one-time google_oauth_setup_inbox.py (drive.readonly grant); until
  then the watcher Degrades to tape-only rather than crashing.
- Processed-tracking is by (file_id, modifiedTime) fingerprint in the state file —
  the raw files are left in place (read-only scope cannot move them), which is also a
  cleaner audit trail.
- For a live end-to-end check the API + key + Drive grant all need to be present
  (not possible in this offline session).

## 2026-09-09 - leasy full-tape reconciliation: tape_fetch_limit=0 ships + end-to-end run

Verified the prior work is in place, closed the remaining steps, and ran the
watcher end-to-end against leasy's FULL tape.

**Prior work confirmed in place:**
- cash_watcher.py: `_FULL_TAPE_LIMIT()` (1,000,000) + `_effective_tape_limit(cfg)`
  (0/negative -> full-tape sentinel; positive passed through). Both
  `_fetch_tape(...)` call sites (detect_changes at line ~173, _poll_once fallback
  at ~427) call `_effective_tape_limit(cfg)`. Docstring updated re: full vs sampled.
- config.yaml sop1.watcher.tape_fetch_limit: 0; config.py WatcherConfig default and
  from_dict both 0.

**Test added:** test_effective_tape_limit_zero_means_full_tape asserts 0 and -1
resolve to _FULL_TAPE_LIMIT() and a positive value passes through (sampled).
Imported _FULL_TAPE_LIMIT/_effective_tape_limit into tests/test_cash_watcher.py.

**Quality gate:** pytest tests/test_cash_watcher.py tests/test_drive_inbox.py = 21
passed (was 20, +1 sentinel test). ruff check . = clean. Nothing my edits broke.

**OCR check:** tesseract is NOT on PATH but exists at
C:\Program Files\Tesseract-OCR\tesseract.exe (resolved by ocr.py
_WINDOWS_TESSERACT_PATHS fallback). tesseract_available() -> True; deps
(pytesseract, pypdfium2, PIL, pypdf) all import. extract_pdf() on the retained
scanned statement returned parsed bank rows with ocr=True. Scanned PDF IS
readable through the OCR path.

**End-to-end run (full tape):** `.venv\Scripts\python.exe cash_watcher.py
--once --borrower leasy` against the live redshift-api (46 borrowers, leasy
present). State rows went 1000 (sampled) -> **5161 (full tape)**; watermark
d2b3bc5b... -> 9da1d343...; latest_date 2026-07-22 -> 2026-09-02. New report
out/leasy/run_c7a70a61aeb3.json: independent_source_present=true,
reconciliation_scope="verified against independent bank/mobile statement side",
bank_statement_count=50, rows_ingested=10265 (5,161 tape rows x2), coverage 100%,
cash_total=153,394,549.57 USD (currencies PEN+MXN, live FX as of 2026-09-08).
Raw statement retained at out/leasy/statements/bank_statement_scanned_image.pdf
(audit trail). Second --once poll = "no changes" (stable full-tape watermark) and
no re-download (fingerprint already ingested) -> idempotency confirmed.

**Cleanup:** removed temporary _probe_leasy.py. Re-ran quality gate after cleanup:
21 pytest passed, ruff clean.

**Guardrails:** read-only only (SELECT via redshift-api /query); no DML/DDL; all
work inside verifications-automation; used .venv\Scripts binaries exclusively.

## 2026-09-09 - OCR diagnostic on leasy statement: extraction is EXACT; source is a sample/mismatch

Follow-up to the full-tape reconciliation. The leasy scanned statement
(out/leasy/statements/bank_statement_scanned_image.pdf) was examined in detail
to determine whether the huge tape-vs-statement variance was an OCR/recovery
problem or a source problem.

**Diagnostic run (read-only, .venv):**
- PDF is 2 pages (portrait 1241x1754), fully scannable.
- Raw OCR via ocr_extract_text() recovered the complete text layer (2,730 chars,
  56 lines, 114 numeric tokens including running balances).
- The OCR text shows a statement header "APEX STERLING BANK" / customer
  "Jane Doe" / 1001 Pinehurst Ave, San Francisco -> clearly a SAMPLE / fictional
  personal retail statement, Statement Period Jun 01-Jun 30 2026, acct *******1294.

**Result: OCR extracts properly and COMPLETELY.**
- extract_pdf() recovered exactly 25 statement rows (17 on p1, 8 on p2) - 100% of
  the transactions on the scan, none dropped.
- 10 deposits = $12,466.98 total; 15 withdrawals = $889.93 total. These EXACTLY
  match the statement's own printed ACCOUNT SUMMARY (TOTAL DEPOSITS $12,466.98,
  TOTAL WITHDRAWALS $889.93, ENDING BALANCE $17,009.15). Zero OCR loss.
- Hence the pipeline's calculated_collections=12,466.98 and
  calculated_disbursements=889.93 are correct for this source.

**Conclusion - this is a source mismatch, NOT an OCR/pipeline defect.**
- The uploaded file is a sample personal-account statement (payroll, coffee,
  groceries, Zelle, ATM), unrelated to leasy's loan book.
- The loan tape (5,161 loans -> 10,265 transactions; ~$80.8M collections /
  $72.6M disbursements) cannot reconcile against it - hence the ~3,000x
  variance exceptions in run_c7a70a61aeb3. The reconciliation still runs
  (independent_source_present=true) but the source is not leasy's operating/
  collection account.
- No OCR->JSON restructure is warranted: the two-stage OCR->JSON->calculate
  design is already in place and proven exact on this scan.

**Flagged action:** the leasy Drive inbox folder (1ck-AX_01AOZNIwpB-vFjCEaWz7KDZYwU)
needs a CORRECT operating/collection-account statement uploaded for leasy to get a
meaningful independent reconciliation. Treat the current 5,016 variance exceptions
as reflecting a wrong-source sample, not a real accounting discrepancy.

## 2026-09-09 (follow-up) � "HTTP Error 500" diagnosed: NOT the full-tape limit; Redshift Serverless is flapping. Full-tape re-run still reconciles.

**Symptom:** `cash_watcher.py --once --borrower leasy` returned "HTTP Error 500" on
the tape fetch twice in a row, after the same limit=1000000 fetch worked at 08:18.
Hypothesis (from the prior session): the large full-tape request might be the cause.

**Probe (read-only, .venv, live API at http://127.0.0.1:8001):**
- `/loan-tape?borrower=leasy&limit=100|1000|100000|1000000` -> ALL FOUR returned the
  *same* failure (at the time: `404 Unknown borrower 'leasy'. Available: ` with an
  empty list). A limit-induced 500 would not fail identically at limit=100.
- `/health/db` -> `503 Database unreachable: connection to ... port 5439 failed:
  Connection timed out (10060)`; `Test-NetConnection` to the endpoint: TCP + ping
  both failed.
- `/borrowers` -> `{"borrowers":[]}` while the DB was down (the server-side
  `_load_borrower_names` refresh query fails and returns the stale cache, which was
  empty). When the borrower cache is still warm, `/loan-tape` instead fails later at
  `db.fetch_all` -> the real `500 Query failed: <connection error>` the watcher saw.

**Conclusion � the 500 is a DB outage, not a limit problem:**
- Server-side `redshift-api/.env` has `MAX_RESULTS_LIMIT=2000000` (confirmed),
  comfortably above the watcher's `_FULL_TAPE_LIMIT()` of 1,000,000.
- The endpoint's resolved IP changed during the session (public 44.254.226.217 ->
  private 10.1.56.196), with `Connection timed out` / `SSL connection has been
  closed unexpectedly` / `unrecognized SSL error code: 6` at different moments � a
  Redshift Serverless workgroup that is pausing/resuming/reconfiguring, i.e. a
  genuinely flapping infra dependency. The DB recovered briefly (09:15, /health/db
  200; full tape fetched at 09:18) and dropped again (09:26/09:28).
- No code change to the fetch path is warranted: no chunked fetch, no
  `_FULL_TAPE_LIMIT` reduction (either would turn the watcher into a sampled
  detector to fix a problem that isn't the limit). The watcher already degrades
  correctly (per-borrower fetch error -> "no changes this poll", watermark kept so
  a later poll retries).

**Code change made (observability, tiny + tests):** `cash_watcher._api_get` now
catches `urllib.error.HTTPError` and re-raises a `RuntimeError` carrying the API's
own JSON `detail` (e.g. `HTTP 500 from /loan-tape?...: Query failed: ... SSL
connection has been closed unexpectedly`) instead of the bare `HTTP Error 500`.
Verified live: the watcher's log line now names the actual DB error and the endpoint
IP, so the next outage is self-diagnosing from the watcher log alone. New tests:
`test_api_get_surfaces_server_error_detail`, `test_api_get_surfaces_non_json_error_body`.

**Real defect found during verification and fixed (state clobber):** while
re-verifying the cross-file dedup, the retained-statement dir had grown to 7 files
(6 real BBVA PEN monthly statements + the sample) and the run log showed
`dropped 25 duplicate row(s) ... 207 unique row(s)` � the dedup collapsed the
sample's 25 rows that were parsed twice in one cycle. Root cause of the
double-parse: `detect_changes` rebuilt each borrower entry from scratch, silently
**dropping `ingested_statements`** (the Drive drop-folder idempotency record), so
every change-triggered run treated every inbox file as new and re-downloaded +
re-parsed it (the dedup then masked the double-count). Fix: `detect_changes` now
preserves `ingested_statements` from the previous entry when rebuilding it. New
test: `test_detect_changes_preserves_ingested_statements`. Verified: a forced
re-run after the fix no longer re-downloads (no "ingested N new statement file(s)")
and produces the same 207-row independent side.

**Full-tape re-run result (run_9dbdbc5d52b6, 09:18, while the DB was briefly up):**
- Tape: full 5,161 rows fetched at limit=1000000 (rows_ingested 10,422 normalized
  events), watermark unchanged (9da1d343...), status done.
- `independent_source_present=true`, `reconciliation_scope="verified against
  independent bank/mobile statement side"`, `bank_statement_count=207`.
- Independent side now includes 6 REAL leasy operating-account statements (BBVA PEN
  monthly JAN-MAR-APR-MAY-JUN 26, text-layer, confidence 1.0, 25-35 rows each =
  182 rows) plus the fictional sample (25 rows, OCR conf 0.6) � all de-duplicated.
  calculated_collections=27,221,932.31 USD / calculated_disbursements=23,744,807.69
  / calculated_cash_total=50,966,740.00 (PEN+MXN, live FX as of 2026-09-08) vs
  tape reported collections 80,767,532.26 / disbursements 72,627,017.31 /
  cash_total 153,394,549.57. 5,027 exceptions (variance remains large because ~6
  months of operating flows vs a cumulative loan book � but the independent side is
  now REAL operating data, addressing the prior "source is a sample" caveat).
- The sample statement is counted exactly once (25 rows) � the original 50-row
  double-count is gone.

**Quality gate:** tests/test_cash_watcher.py + tests/test_drive_inbox.py = 27
passed (was 24, +2 api_get, +1 state-preservation); full suite 235 passed; ruff
clean on cash_watcher.py + both test files.

**Open / needs the user or infra team:** the Redshift Serverless workgroup was
unstable (public->private IP flip, SSL errors). RESOLVED on its own: the DB came
back up (~10:17) and the final confirmation run completed (see below). The
endpoint flapping itself (public 44.254.226.217 -> private 10.1.56.196, SSL
errors) is worth a glance by whoever owns `workgroup-xlarge.897058370678.us-west-2`
(repeated pause/resume can look exactly like this); the watcher code needs nothing.

**Final confirmation run (run_d2c8f90ac684, 10:18, DB up):** `--once --borrower
leasy` re-ran the full-tape reconciliation to completion:
- status done; full 5,161-row tape fetched at limit=1000000 (rows_ingested 10,422
  normalized events, coverage 100%), watermark restored to 9da1d343... (tape
  unchanged).
- independent_source_present=true; bank_statement_count=207 (6 real BBVA PEN
  statements 182 rows + sample 25 rows, de-duplicated; sample counted exactly
  once).
- calculated_collections=27,221,932.31 / calculated_disbursements=23,744,807.69 /
  calculated_cash_total=50,966,740.00 USD vs tape 80,767,532.26 / 72,627,017.31 /
  153,394,549.57; 5,027 exceptions � byte-identical to run_9dbdbc5d52b6 (09:18),
  confirming determinism and reproducibility.
- No statement re-download occurred (no "[bank] ingested N new statement file(s)"
  line) � the ingested_statements-preservation fix held: the 7 retained files were
  re-parsed locally only.
- State: rows 5161, watermark 9da1d343..., last_run_at 10:18:47, ingested_statements
  (7 fingerprints) preserved.

## 2026-09-09 (follow-up) � leasy variance attribution: 5,027 exceptions decomposed

Per user request ("do 1"), ran a read-only attribution of the 5,027 exceptions in
run_d2c8f90ac684 to separate real signal from artifacts. Result: the count is
~99.9% anomaly-rule noise on the tape side; the 3 real reconciliation variances
are much larger than the report showed once a currency bug is fixed.

**Finding 1 � currency bug (real, fixed):** the BBVA Peru statements label their
currency as `MONEDA: SOLES`, but `detect_shape`'s `_CURRENCY_RE` only matched ISO
codes (`\b(PEN|USD|...)\b`), so the BBVA rows carried a blank currency and were
summed as if already-USD. Fixed: `_CURRENCY_RE` now also matches `SOLES`/`DOLARES`
(case-insensitive) and `detect_shape` maps them to PEN/USD via `_CURRENCY_ALIASES`.
New test `test_detect_shape_maps_spanish_currency_labels`. Effect: the calculated
(independent) side drops from 27.2M "USD" (actually PEN) to the true **8.1M USD**.
Report regenerated: `run_f5f5d05cca70` (status done, independent=true,
bank_statement_count=207).

**Finding 2 � honest reconciliation variances (run_f5f5d05cca70):**
- collections: reported 80,767,532 vs calculated **8,119,984 USD** ? 894.7% (was
  mis-stated as 196.7% in the pre-fix report)
- disbursements: 72,627,017 vs **7,075,789 USD** ? 926.4%
- cash: 153,394,550 vs **15,195,773 USD** ? 909.5%

**Finding 3 � why the variance is this large (the substance):**
1. **Period mismatch (dominant):** the tape spans 2018-09-25 .. 2026-09-02 (an
   8-year cumulative loan book, 10,215 events) while the statements cover only
   Jan-Jun 2026 (207 rows). Total-vs-total inherently mixes the two.
2. **Even month-by-month in the overlap, tape > bank ~2-3x:** tape-derived
   collections $2.5-5.8M/mo vs actual bank inflows (via Kushki) $1.3-1.5M/mo for
   2026-01..06. The tape's "collections" are DERIVED (`total_loan_amount` minus
   outstanding, an estimate that counts scheduled-not-yet-collected amounts), not
   actual cash.
3. **The account is an operating/treasury account, not a pure collections
   account:** $8.1M in via **Kushki** (payment processor � the real collection
   channel), $7.0M out as **internal Leasy funding transfers** ("OP FX/TC PF LEASY
   II", 2.2-5.4M PEN each � treasury movement, not expense), plus small fees/taxes.
   So a direct "collections vs cash-in" comparison against ONE account is the wrong
   scope until the full operating-account set is in.

**Finding 4 � the 5,024 anomaly exceptions are tape-side noise, not signal:**
- **roundtrip 3,334 � 100% artifact.** `normalize_loan_tape_row` dates a
  collection at `closure_date` ? `company_due_date` ? **fallback `begin_date`**
  (ingest.py line ~159). 3,334 of 5,054 loans with a collection event have BOTH
  closure and company-due dates blank, so the collection is dated at begin_date �
  identical to the disbursement date � fabricating "same-day disbursement +
  collection." Verified exactly 3,334 = the full roundtrip count. Zero real
  same-day round-tripping (only 6 loans genuinely close on their begin date).
- **vol 820 / round 599 / seqjump 242 / microsplit 29** � portfolio-natural
  patterns on the tape rows (large loans exceed the median-based volume threshold,
  loan sizes are round, many tape events share a synthetic `account_ref='redshift'`
  + date so the same-day microsplit rule groups them). Not laundering signal.

**Recommendations (not yet implemented � flagging for a decision):**
1. Fix the collection-date fallback: when a loan has no closure/company-due date,
   do not fabricate `begin_date` as the collection date (leave undated / flag),
   which removes the 3,334 false roundtrips at the source.
2. Decide whether tape-side anomaly rules (vol/round/seqjump/microsplit) belong on
   the tape at all, or should be scoped to the bank side where structuring
   patterns actually live � the tape is a reported snapshot, not a transactions
   ledger, so most "anomalies" there are product-natural.
3. The real business question to take to loan ops: the ~3x gap between
   tape-derived collections and actual Kushki inflows in the covered months � is it
   (a) other collection channels/accounts not yet in the inbox, (b) DPD loans
   counted as collected-to-date by the derivation, or (c) genuinely uncollected?
   That decides whether the reconciliation needs more statement coverage or a
   corrected collection definition.

**Quality gate:** full suite 237 passed, ruff clean on the whole project. Scratch
attribution script removed (findings recorded here).

## 2026-09-09 (follow-up) � report now shows the statement-side breakdown + email delivery

Per user request ("is it possible to get this in the report" / "can i have the
report sent to my email"), two additive features:

**1. Statement-side breakdown in the working paper.** The watcher now computes a
`statement_breakdown` (cash_watcher._statement_breakdown) for every run and stores
it in `run.inputs["statement_breakdown"]`; reporter.py renders a new
"## Statement-side breakdown (independent bank/mobile vs tape)" section in the .md
when present (absent = backward compatible). It shows, all FX-converted to USD:
- totals (bank in / out / total, row count)
- per-statement file (rows, in, out)
- per-category (deterministic classification: kushki payment-processor / leasy
  internal funding transfer / itf tax / bank fee / other)
- per-month tape vs bank (so the SOP-1 period-mismatch story is visible in the
  report itself, not just an ad-hoc analysis)
Verified live on leasy (run_2a90ea981cd2): totals bank in 8,119,984.02 / out
7,075,788.89, Kushki 8.1M in, internal transfers 7.0M out, monthly table renders.
Tests: test_reporter.py::test_statement_breakdown_rendered_in_markdown_when_present.

**2. Report email delivery (Gmail API via OAuth � no password).** New
`phase0_foundations/gmail_send.py` (Gmail API `gmail.send` scope, mirroring the
Drive-inbox OAuth pattern: google-auth + googleapiclient, silent token refresh)
and `phase0_foundations/emailer.py` (composes the .md + .json as a MIME message,
delivers via the Gmail API). New `EmailConfig` nested under `WatcherConfig`;
config.yaml `sop1.watcher.email` block (from_addr/to_addrs/subject_prefix +
gmail_token_env); token path read from .env at send time via
`GOOGLE_OAUTH_GMAIL_TOKEN_JSON`. One-time setup script
`scripts/google_oauth_setup_gmail.py`. Wired into cash_watcher: `_send_run_email`
after every change-triggered done run emails the run's .md + .json (the disk write
remains the audit trail; email is additional delivery; outage is logged, never
fatal). Tests: tests/test_emailer.py (7: config, unconfigured skips, success
delivers raw MIME with both attachments, missing files skipped, Gmail failure
raises) + tests/test_gmail_send.py (3: missing-token error, expired-token refresh,
API send payload) + tests/test_cash_watcher.py (2 glue tests).

**Transport decision (user): SMTP app password was the first plan, but the user
cannot create one � nephy@lendable.io is corporate Google Workspace and the
domain admin has disabled app passwords. Switched to the Gmail API / OAuth path
(no password, one-time browser grant), which reuses the project's existing Google
OAuth client.** Gated per repo rule: `enabled: true` in config but the send only
becomes real after the user runs `python scripts/google_oauth_setup_gmail.py`
(and the Gmail API is enabled on the Cloud project for that OAuth client); until
the token exists the watcher logs a clear "run google_oauth_setup_gmail.py"
message instead of sending. Then one live run confirms delivery.

**Live verification (2026-09-10, confirmed):** the user completed the one-time
Gmail grant (`python scripts/google_oauth_setup_gmail.py`, browser consent for
gmail.send) and the token saved with a refresh token. A forced leasy watcher run
then sent the working paper for real via the Gmail API:
`[run] leasy -> out/leasy/run_51e985dbb747.json ... [mail] leasy: working paper
emailed to nephy@lendable.io`. Full path verified end-to-end: fetch full tape ?
pipeline ? report ? Gmail API send (no SMTP, no password).

**Quality gate:** full suite 258 passed, ruff clean on the whole project. leasy
report regenerated end-to-end (run_2a90ea981cd2) with the new section.

## 2026-09-10 � statement uploads are now their own trigger (no human in the loop)

Per user request ("if i upload statements to a random folder, it should be able
to do this without any human in the loop"): before this change, a run only fired
when the loan TAPE changed � statements were fetched opportunistically during a
tape run, so uploading a statement alone did nothing. Now a statement upload is a
first-class change trigger:

- New `cash_watcher._detect_new_statements(cfg, state, borrowers)`: per poll,
  lists each borrower's configured Drive drop-folder and returns borrowers whose
  inbox holds files whose (file_id, modifiedTime) fingerprint is not yet recorded
  as ingested. `detect_changes` adds those borrowers to `changed` even when the
  tape watermark is unchanged, so `_poll_once` runs the pipeline (ingests the new
  statements, reconciles, writes the report, emails the .docx working paper).
- Fully read-only (only lists the Drive folder); tolerant � an unconfigured/
  inaccessible inbox just contributes no statement-triggered runs (tape-only),
  never an error. Ingestion idempotency still holds: fingerprints are recorded
  once ingested, so a file is not re-triggered/re-downloaded on later polls.
- Retry semantics unchanged: if the run fails, the tape watermark is popped and
  the next poll retries (statements already retained are re-parsed locally).

**Requirements for the no-human flow to work:**
1. Statements must be uploaded to the borrower's CONFIGURED Drive subfolder
   (config.yaml sop1.watcher.bank_statements.folder_by_borrower.<borrower>, or
   inbox_folder fallback) � a "random" folder is never scanned.
2. The watcher must actually run on a schedule (Task Scheduler `--once` every N
   minutes, or `--interval` loop); "immediate" = within the poll cadence.
3. `bank_statements.enabled: true` and the Drive inbox token present.
No human review gate blocks the run/report/email; human review is a downstream
step, not a blocker.

Tests: tests/test_cash_watcher.py +3 (flags un-ingested only via configured
folders, tolerates inbox errors, statement-triggers change when tape unchanged).
Quality gate: full suite 265 passed, ruff clean.

## 2026-09-10 � cloud (GitHub-hosted runner) scheduling groundwork

Per user choice ("Cloud (hosted runner)"), started the pieces that make the
watcher run on a GitHub-hosted (ephemeral) runner. Two hard requirements drove
the design: (1) the redshift-api must be publicly reachable (a cloud runner
can't see 127.0.0.1:8001), and (2) ephemeral runners wipe the filesystem each
run, so the watcher's state + retained statements must persist somewhere.

**Added:**
- `phase0_foundations/drive_store.py` � mirrors the watcher's `out/` directory
  to an app-created Google Drive folder ("cash-watcher-state", under
  DRIVE_FOLDER_ID or My Drive root) using the existing drive.file-scoped OAuth
  token (same as gsheet_export). `pull_dir` restores files missing/different by
  content md5; `push_dir` uploads new/changed files (create or update), skipping
  unchanged. Names are flat Drive names with '/' encoding subpaths; traversal
  names are rejected.
- `cash_watcher.py --drive-sync` � pulls `out/` from Drive before a poll and
  pushes it back after (`--once` and loop mode); sync failures are logged and
  never fatal, so a Drive hiccup can't kill the watcher.
- `.github/workflows/cash-watcher.yml` � `*/10` cron (+ workflow_dispatch),
  checkout ? setup-python 3.11 ? pip install -r requirements.txt +
  tesseract-ocr ? writes the OAuth files + .env from GitHub secrets ? runs
  `python cash_watcher.py --drive-sync --once` ? uploads `out/` as a 30-day
  artifact.
- `docs/cloud-runner-setup.md` � architecture, host recommendation (Railway
  recommended / Fly.io alternative; avoid Render free tier � it sleeps), the
  redshift-api env vars to set on the host, the full GitHub-secret list, and
  enable steps.

**Host recommendation (user asked "help me pick a host"):** Railway (easiest,
~$5/mo, always-on, HTTPS) or Fly.io (cheapest always-on, ~$2-5/mo). Render free
tier sleeps after ~15 min idle � wrong for a scheduled poll. Redshift's public
endpoint is internet-reachable so no VPC peering is needed; the Serverless
workgroup must be running when the poll fires (it auto-resumes on connect).

**NOT yet done (needs the user/infra):**
1. Deploy the redshift-api to the chosen host (env vars in docs/cloud-runner-setup.md).
2. Add the six GitHub secrets (REDSHIFT_API_URL, REDSHIFT_API_KEY,
   GOOGLE_OAUTH_CLIENT_JSON, GOOGLE_OAUTH_TOKEN_JSON,
   GOOGLE_OAUTH_INBOX_TOKEN_JSON, GOOGLE_OAUTH_GMAIL_TOKEN_JSON).
3. Push `.github/workflows/cash-watcher.yml` to the deployed repo and trigger the
   first run.
4. Scope `sop1.watcher.borrowers` for the cloud schedule (empty = all 46, heavy
   for a 10-min cron).

Tests: tests/test_drive_store.py +4 (folder create/reuse, pull missing/unchanged,
traversal guard, push new/update/skip). Quality gate: full suite 271 passed,
ruff clean.

## 2026-09-10 � cloud deploy status: BLOCKED on Redshift network reachability

Progress on the cloud-runner path:
- **redshift-api deployed to Railway** (project redshift-api-lt, service
  redshift-api-lt-production, URL https://redshift-api-lt-production.up.railway.app).
  Added a Dockerfile (python:3.11-slim, honors Railway's $PORT), .dockerignore
  (excludes .venv/logs/.env/tests) and railway.toml (DOCKERFILE builder +
  /health healthcheck). Deploy succeeds; `Uvicorn running on 0.0.0.0:8080`.
- **Auth verified**: `/health` 200 configured:true; API_KEY matches the local
  `redshift-api/.env` value (SHA-256 confirmed equal).
- **drive_store + --drive-sync + the GitHub Actions workflow + setup doc** are
  all in place (see previous entry).

**BLOCKER (needs AWS/infra owner, not code):** `/health/db` from Railway times
out while the same endpoint works locally. Cause: the Redshift Serverless
endpoint `workgroup-xlarge.897058370678.us-west-2.redshift-serverless.amazonaws.com`
now resolves ONLY to the private IP `10.1.56.196` (earlier it resolved to public
44.254.226.217 during the observed flapping). The local machine reaches it via a
corporate/VPN route to the VPC; Railway (public internet) cannot route to a
10.x address.

Options for the owner:
1. Enable **public accessibility** on the workgroup (AWS Console ?
   Redshift Serverless ? workgroup-xlarge ? Data access ? Publicly accessible,
   or `aws redshift-serverless update-workgroup`). Recommended; the endpoint
   then resolves publicly (like 44.254.x) and Railway connects. Read-only + API
   key protected, but opening to the internet is a security decision.
2. cloudflared tunnel from this machine to the local redshift-api (stopgap �
   reintroduces machine/tunnel dependency).
3. Run the runner inside the VPC (AWS-hosted runner/EC2), avoiding public
   exposure entirely.

Until one of these is decided, the GitHub-hosted cloud schedule cannot complete
a real run; the local watcher keeps working normally.

## 2026-09-11 � SELF-HOSTED GitHub Actions runner live (no admin needed for the schedule)

Per user choice, after the AWS-admin blocker (private Redshift endpoint), went
with a **self-hosted GitHub Actions runner on this machine** � same GitHub
Actions scheduler the cloud version would use, running where the data is:

- **Runner installed + registered**: `C:\actions-runner` (v2.337.0), registered
  to `nephy-edge/verifications-automation` as `verifications-watcher` (labels
  self-hosted/Windows/X64). Currently running as a persistent background process
  ("Listening for Jobs"). Permanent path: a Windows service
  `GitHubActionsRunner-verifications-watcher` (RunnerService.exe) � the one
  admin step (elevated PowerShell New-Service) is still pending; until then the
  background process is the live runner.
- **Workflow `cash-watcher.yml`** (`.github/workflows/`) � `*/10` cron +
  workflow_dispatch; `runs-on: self-hosted`; Windows-safe steps: workspace venv
  (no actions/setup-python � its registry cleanup fails on non-elevated
  self-hosted runners), OAuth files + .env rebuilt from GitHub secrets (pwsh),
  `cash_watcher.py --drive-sync --once`, uploads `out/` artifact.
- **GitHub secrets set** (6): REDSHIFT_API_URL=http://127.0.0.1:8001,
  REDSHIFT_API_KEY, GOOGLE_OAUTH_CLIENT_JSON/TOKEN_JSON/INBOX_TOKEN_JSON/
  GMAIL_TOKEN_JSON (file contents). Values came from local files via stdin,
  never printed.
- **Code pushed** to the deployed repo (commit 889f18a + 0be1f6a after rebasing
  over two remote commits 4858ddc/9b5e4ff � resolved conflicts with the
  monorepo's authoritative versions).
- **Drive state seeded** (52 files) so the runner resumes from current state.
- `config.yaml` borrowers scoped to `[leasy]` for the schedule.

**Verified end-to-end via GitHub Actions (run 34574723559, success):**
```
[sync] pulled 52 file(s) from Drive
[run] leasy -> out\leasy\run_4caf0df68cb5.json (5027 exceptions, independent=True)
[mail] leasy: working paper emailed to nephy@lendable.io
[sync] pushed 4 file(s) to Drive
```
The forced change (watermark cleared in the Drive-persisted state) triggered a
full reconcile + email through the scheduler; idempotency confirmed on the
previous trigger (`[poll] no changes`, no re-email). Transient Redshift outages
degrade to "no changes" with state preserved, exactly as designed.

**Remaining small steps (optional):**
1. Install the runner as a Windows service (elevated command) so it survives
   reboot/logoff without the background process.
2. Auto-start the local redshift-api at logon (currently started manually).
3. Restore the local `cash_watcher_state.json` watermark (the forced-cleared one
   is back in the Drive copy after the successful run � the local file is what
   the manual local watcher uses).

**Scope decision (user, 11:45): cut-off-date synchronization of the statement side
is NOT being built.** Rationale: there is no real-time monitoring/cadence mechanism
in place yet, so the pipeline cannot reliably know when statement data is "as-of";
enforcing a cut-off alignment on the independent side would be premature. Consequence:
variance attribution (below) compares the statement-covered window manually, not via
auto-aligned cut-offs. The tape-side cut-off guard already shipped (hard_stop/backdate)
stays as-is.

## 2026-09-15 � Transaction Matching tab: scanned numbered-table statements now recover real column names

Per user picking this from the Part-1 gap list ("Scanned numbered-table OCR recovery for the Transaction Matching tab"). Closes the residual gap logged 2026-09-01: `extract_pdf_table_rows()` (the Transaction Matching tab's column-picker source) recovered real transaction ROWS from a scan (via `extract_pdf`'s canonical fallback) but only ever exposed the 5 generic columns (`Date/Description/Amount/Direction/Currency`) � so you couldn't pick the file's real reference column (e.g. `TRANSACTION CODE`) to match on.

**Change** (`phase1_ingestion_parsing/extract.py`):
- New `_extract_positioned_table_rows(path, account_ref)` � recovers numbered-table statements' real column names (`#/DATE/NARRATION/DEBIT/CREDIT/BALANCE`) from the *word coordinates*, reusing the already-tested `_table_header_columns`/`_map_line_to_columns`/`_is_noise_line` (the same coordinate logic `_scan_numbered_table` uses). A blank-text scan is OCR'd into a searchable PDF via the existing `ocr_to_searchable_pdf`, then the coordinate mapper runs against its positioned text layer; a PDF that already has a text layer uses the native coordinates directly (no OCR cost). Temp file cleaned in `finally`. Returns `None` cleanly when the layout isn't numbered-table or OCR is unusable, so callers fall through unchanged.
- `extract_pdf_table_rows()` gained a third fallback tier before the canonical 5-generic-column fallback: ruling-line table ? positioned/OCR coordinate path ? canonical.
- Added a cross-reference comment in `_extract_positioned_table_rows`'s row/continuation loop pointing at `_scan_numbered_table`'s equivalent logic (a /review uncommitted pass flagged the two as near-duplicates with drift risk; chose the cheap comment over refactoring the validated canonical parser).

**Verified**: 3 new tests in `tests/test_extract.py` � scan?OCR recovers real columns + two rows, native text layer recovers real columns WITHOUT calling OCR (`mock_ocr.assert_not_called`), and a no-OCR scan falls cleanly to canonical (never crashes/empty-header). Full suite 266 ? **269 passed**, ruff clean. Change is in the working tree (the folder syncs to the deployed repo separately; not pushed yet).
