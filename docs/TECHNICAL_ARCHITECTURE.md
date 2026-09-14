# Verifications Automation — Technical Documentation

> **Who this is for:** developers and reviewers who need to understand how this
> project is built — the architecture, the pipeline, the modules, the
> configuration, and how to run/deploy/test it. For **how to use the tool**,
> see `docs/USER_GUIDE.md`.

## 1. Overview

`verifications-automation` implements Lendable's loan-verification workflow:
ingest a loan tape and bank/mobile-money statements, compute independent
aggregates deterministically, reconcile them against reported figures using
encoded thresholds, detect anomalies, generate working-paper reports, and drive
a human review/sign-off loop — with a feedback/hardening loop.

The project follows a firm design rule:

> **The LLM never does arithmetic; code never invents facts or judgement.**
> Deterministic SQL/rules/arithmetic are the spine. The LLM (where enabled) is
> reserved for genuine interpretation (PDF parsing fallback, anomaly narrative,
> read-only NL querying), and a human for sign-off.

## 2. Repository layout

```
verifications-automation/
├── phase0_foundations/             # config, logging, models, FX, Drive/Gmail/db foundations
├── phase1_ingestion_parsing/       # multi-format ingest + PDF/OCR extraction + cash-map + vehicle lookup
├── phase2_verification_engine/     # deterministic aggregation + reconciliation + transaction matching
├── phase3_anomaly_reporting/       # anomaly detection, ranking, working-paper reports
├── phase4_human_review/            # review/approval + read-only NL querying
├── phase5_feedback_hardening/      # feedback loop + rule hardening
├── app/streamlit_app.py            # Streamlit UI (5 tabs)
├── cash_watcher.py                 # headless change-detection watcher (SOP 1) + scheduler entrypoint
├── runner.py                       # end-to-end CLI entrypoint
├── config.yaml                     # thresholds + all data-driven config (single source of truth)
├── requirements.txt / requirements-dev.txt / packages.txt
├── .env.example
├── docs/                           # PROGRESS.md, USER_GUIDE.md, TECH doc, surveys
├── .github/workflows/cash-watcher.yml
└── tests/
```

Phase directories use underscores so each is a directly importable Python
package (e.g. `from phase2_verification_engine import reconcile`).

### Mapping phases to workflow steps

| Phase | Package | Responsibility |
|-------|---------|----------------|
| 0 | `phase0_foundations` | Config loading, append-only jsonl logging, data models, FX, Drive/Gmail/Redshift foundations |
| 1 | `phase1_ingestion_parsing` | Multi-format normalization, PDF/OCR extraction, cash-map parsing, vehicle-registry client |
| 2 | `phase2_verification_engine` | Independent aggregates, reconciliation, asset verification, transaction matching |
| 3 | `phase3_anomaly_reporting` | Anomaly rules, risk ranking, working-paper reporter |
| 4 | `phase4_human_review` | Review/approval state machine + read-only NL "Ask" querying |
| 5 | `phase5_feedback_hardening` | Feedback capture + rule promotion |

## 3. The verification pipeline (end to end)

**CLI (`runner.py`)**: read tape + statements → normalize to canonical rows →
compute independent aggregates → reconcile against reported figures →
detect anomalies → rank → build working paper (`.md` + `.json`) → append audit
log.

**Streamlit app (`app/streamlit_app.py`)**: the same pipeline, driven from the
"Run verification" tab, persisted under `out/<run_id>/`.

**Watcher (`cash_watcher.py`)**: polls the Redshift Query API per borrower,
detects loan-tape changes via a content watermark and new Drive-inbox
statements, and re-runs the same pipeline headless whenever a change is
detected — generating a working paper and emailing it.

### 3.1 Ingestion & parsing (Phase 1)

- `ingest.py` — `load_and_normalize()` turns loan tape / bank / mobile / ledger
  / cash-map files into **canonical rows**. Handles several real bank schemas
  (debit/credit-split, single-signed-amount, title-row/spacer-row header
  auto-detection). `normalize_loan_tape_row()` derives disbursement/collection
  events from per-loan snapshots, respecting per-borrower column overrides.
- `extract.py` — PDF extraction supporting multiple real layouts:
  1. single-date,
  2. two-column-date (BBVA-style, with running balance),
  3. ISO-date with running balance,
  4. **positional** numbered-table parsing (`_scan_numbered_table`) using word
     coordinates (Absa/Mono-style).
  Extracts per-row currency from the detected shape, computes a **confidence
  score via balance tie-out** (summed debits/credits vs declared totals, or
  opening+net==closing). Anything below the confidence floor is flagged for a
  manual spot-check.
- `ocr.py` — OCR fallback for scanned/image-only PDFs (Tesseract). Includes a
  **searchable-PDF intermediate** (`ocr_to_searchable_pdf`) so a scan of a
  numbered-table layout can be re-parsed positionally. OCR rows are capped at
  confidence 0.6.
- `cashmap.py` — parses cash-map questionnaires (free-text, `txt`/`html`/`pdf`/
  `docx`/`json`) into `{borrower_name, accounts, source}` and matches uploaded
  statements to accounts.
- `vehicle_verify.py` — pluggable registry-API client (Verifik), with
  **confirmed-only** country contracts (PE/CO/CL/AR/BR live; MX/EC mock/refuse
  to guess). `make_client()` returns `VerifikClient` when `VERIFIK_TOKEN` is
  set, else `MockVehicleClient`.
- `assets.py` — `load_expected_assets()` / `load_registry_results()` load the
  two independent sources for asset-existence checks (A2): a reported
  collateral register and a third-party registry's own findings, which can
  come from a live Verifik lookup or from a pre-run file produced by the
  sibling `apps/vehicle_plate_peru` tool.
- `methodology.py` — the `Methodology` class: collections/disbursements/cash-
  balance definitions and sample-vs-population policy (A5), encoded as data
  rather than left as tribal knowledge.
- `fx_rates.py` — live FX lookup (`fawazahmed0/currency-api`). Note this
  lives here, in `phase1_ingestion_parsing`, not in `phase0_foundations`,
  despite pairing conceptually with `fx.py`.

### 3.2 Aggregation, reconciliation & anomaly detection (Phases 2–3)

- `calculate.py` — `calculate_aggregates()` produces independent
  collections / disbursements / cash totals, **optionally FX-normalized to a
  base currency** (see §6). Deterministic only.
- `reconcile.py` — compares reported vs. calculated figures against the
  `thresholds` block and raises `ExceptionItem`s (collections variance,
  disbursement absolute/pct, cash balance variance).
- `transaction_match.py` — `match_transactions()` / `build_match_report()`:
  row-level exact/partial matching between two sources on a chosen reference
  column, with configurable case-sensitivity and punctuation handling; backs
  the Tab 1 "detailed transaction matching" drill-down.
- `assets.py` — `verify_asset_existence()`: compares the reported asset
  register against independent registry results (from phase1's `assets.py`),
  raising `not_checked` / `check_failed` / `owner_mismatch` findings.
- `anomaly.py` — anomaly rules (duplicate transactions, round-dollar amounts
  gated on materiality, transaction volume, sequence jumps, off-hours,
  micro-split/structuring, same-day round-tripping). Every rule attaches the
  canonical record key(s) as **evidence**.
- `rank.py` — normalizes and sorts severity; `forensic_route()` filters items
  at/above the `anomaly_score_high` threshold into a forensic review queue.
- `reporter.py` — `build_report()` renders working papers (Markdown + JSON,
  plus a `.docx` rendering via `report_docx.py` for email — see §3.4):
  aggregates, exceptions with evidence, coverage, forensic queue, currency
  unmapped warnings, and statement-side breakdowns.

### 3.3 Human review & hardening (Phases 4–5)

- `approval.py` — review state machine (pending → reviewed → approved/rejected
  → sign-off), fully logged. This is the in-app flow; the CLI/watcher's
  separate hash-verified sign-off is `phase0_foundations/signoff.py` (§3.4).
- `query.py` — the "Ask (AI)" tab: grounded NL querying over the active run
  (`answer_question()`), backed by `phase0_foundations/llm_client.py`, with
  an optional read-only Redshift drill-down tool.
- `harden.py` — feedback capture + rule promotion, logged; reachable from the
  audit tab.

### 3.4 Foundation services (Phase 0)

- `config.py` — typed dataclasses loaded from `config.yaml`.
- `log.py` — append-only jsonl audit log (`verifications.log.jsonl`).
- `fx.py` — static rate table (live lookup is `fx_rates.py`, which lives in
  `phase1_ingestion_parsing` — see §3.1).
- `drive_store.py` — mirrors `out/` to a Google Drive folder (`drive.file`
  scope) for ephemeral-runner persistence.
- `drive_inbox.py` — read-only (`drive.readonly`) Drive inbox reader for the
  statement drop-folder.
- `gmail_send.py` + `emailer.py` — report email delivery via the Gmail API
  (OAuth, no SMTP password).
- `report_docx.py` — renders the working paper as a `.docx` (plus an HTML
  email body); this, not the raw Markdown, is what actually gets emailed.
- `llm_client.py` — provider-agnostic chat client (OpenAI / Anthropic /
  DeepInfra, with tool-calling) — the one place any LLM call is made from;
  backs the Ask (AI) tab (`query.py`) and the anomaly narrative.
- `signoff.py` — a separate, hash-verified sign-off used by the CLI/watcher
  (`sign_off_run()` / `verify_signoff()`), distinct from the in-app review
  flow in `approval.py`; detects if a report was edited or regenerated after
  sign-off.
- `gsheet_export.py` — exports a loaded table (e.g. a Redshift loan tape) to
  a native Google Sheet via OAuth user consent; backs the "Download as
  Google Sheet" button in the app.
- `metrics.py` — coverage, lead-time, last-verification-per-borrower metrics.

## 4. The Streamlit app (5 tabs)

Defined in `app/streamlit_app.py`. A persistent **sidebar** (present on every
tab) loads a past run by ID and shows the live `config.yaml` thresholds.

1. **Run verification** — upload sources (tape file + Redshift dropdown with
   date-range/max-rows filters and a post-load summary/browse panel, bank
   statements incl. images, mobile-money [csv/xlsx only], ledger, cash map
   with account-mapping) → run pipeline → results, forensic queue, mandatory
   fraud referrals, warnings → optional "download as Google Sheet" →
   detailed transaction matching (reuses the uploaded files; column picker +
   matching options [case sensitivity, punctuation, partial match] + totals +
   match/unmatched).
2. **Asset & vehicle verification** — shared country selector (some
   countries need an extra input beyond the plate); two modes: *verify a
   reported asset register* (registry data via **live lookup or an uploaded
   pre-run registry-check file** → findings flow into Review) and *quick
   lookup / spot check*.
3. **Review & sign-off** — approve/reject + Head-of-Risk sign-off through
   `approval.py` (see also the separate CLI/watcher sign-off, §3.4).
4. **Audit log & hardening** — run log, lead-time, last-verification-per-
   borrower, hardening entries, and a form to record a new hardening
   promotion directly.
5. **Ask (AI)** — grounded NL querying over the active run, optional read-only
   warehouse drill-down.

Design tokens are vendored from the workspace `streamlit_design` skill into
`design_assets/` so the app is self-contained when deployed as a standalone repo.

## 5. The headless watcher (`cash_watcher.py`)

SOP-1 change-detection. Polls the Redshift Query API (via the read-only
`redshift-api` gateway) for each borrower's loan tape and re-runs the pipeline
when the tape **or** the statements change.

- **Change detection** — a content watermark per borrower (row count +
  order-insensitive SHA-256 over canonicalized rows), so Spectrum's
  non-deterministic scan order doesn't cause false re-runs.
- **Statement trigger** — new files in a borrower's configured Drive sub-folder
  are a first-class trigger (no human in the loop).
- **Cut-off guard** — `hard_stop` vs `backdate` modes against `cut_off_date`.
- **Idempotency** — files are fingerprinted (`file_id`, `modifiedTime`) and
  ingested / downloaded once; retries re-parse retained files locally.
- **Delivery** — each change-triggered run writes a working paper and (if
  enabled) emails it via the Gmail API.
- **Sign-off** — `--sign-off` / `--show-signoff` (with `--run-id`,
  `--signed-by`, `--note`) record and verify a hash-based attestation per
  run, independent of the in-app review flow
  (`phase0_foundations/signoff.py`).
- **Persistence** — `--drive-sync` mirrors `out/` + state to Google Drive so an
  ephemeral (GitHub-hosted) runner can resume.
- **Scheduling** — `.github/workflows/cash-watcher.yml` runs it on a `*/10`
  cron against a **self-hosted** runner (because the Redshift endpoint resolves
  to a private VPC IP that a cloud runner can't reach).

Read-only by construction: it only SELECTs via the `redshift-api` gateway and
only lists/downloads Drive files — never DML/DDL, never creates/moves Drive
content.

## 6. Configuration (`config.yaml`)

`config.yaml` is the single source of truth for every tunable. Infra moves
(URLs, key names, hosts) are config-only, never code edits.

- `thresholds` — reconciliation and anomaly thresholds (collections variance,
  disbursement abs/pct, cash variance, forensic score, confidence floor, volume
  multiple, round-dollar materiality, off-hours window, micro-split rules, …).
- `loan_tape_columns.default` + `overrides.<borrower>` — per-borrower column
  mapping into the canonical loan-tape schema; `negative_sign_borrowers` lists
  borrowers whose money columns use a live negative-sign convention (abs()
  applied).
- `sop1.watcher` — enabled/poll interval, state file, tape fetch limit (0 =
  full tape), borrowers, cut-off guard, reported totals, email config, Drive
  statement drop-folder config, `run_on_change` gating, and a tighter
  SOP-1-only `cash_discrepancy_pct` (separate from the shared
  `cash_balance_variance` threshold used elsewhere).
- `fx` — base currency + static rate table (live FX lookup via
  `fawazahmed0/currency-api` wins on overlap; unmapped currencies are surfaced,
  never guessed).
- `llm` — provider/model for the AI features, with separate
  `parse_temperature` / `anomaly_temperature` / `max_tokens` /
  `deepinfra_base_url` (empty provider = rules-only, no outbound LLM calls).
  Keys come from provider env vars.
- `services.redshift`, `assets`, `vehicle_registry`, `logging`, `output` — data-
  driven external endpoints, CDN URLs, Verifik paths, log location, output dir.

## 7. External integrations (read-only / OAuth)

| Integration | Purpose | Scope | Confirmation |
|-------------|---------|-------|--------------|
| `redshift-api` (sibling FastAPI) | Loan-tape fetch, borrower list, read-only query | SELECT-only, schema allowlist, row caps | Live |
| Google Drive (store) | Persist `out/` + state for ephemeral runners | `drive.file` | Live |
| Google Drive (inbox) | Statement drop-folder | `drive.readonly` | Live |
| Gmail API | Email working papers | `gmail.send` | Live (verified send) |
| Verifik | Vehicle registry | read-only | PE/CO/CL/AR/BR live |
| FX (`fawazahmed0/currency-api`) | Live exchange rates | read-only public | Live |
| Google Sheets | Export a loaded table (e.g. a loan tape) on demand | OAuth user consent | Live |

Per the project's contract rule, **nothing goes live on configuration alone** —
each integration is only enabled after a real authenticated call succeeds.

## 8. Environment & running it

Dependencies install **only into the project `.venv`** (never global):

```
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` = runtime deps + tooling (`ruff`, `pytest`).
`requirements.txt` = runtime subset for a bare deploy. `packages.txt` installs
system deps (`tesseract-ocr`) on Streamlit Community Cloud.

**Quality gate** (must be green before "done"):

```
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest
```

**CLI end-to-end:**

```
.\.venv\Scripts\python.exe runner.py --config config.yaml --tape <tape> --statements <statements...> --out out/
```

Other flags: `--ledger`, `--assets`, `--asset-checks`, `--reported` (JSON of
reported figures), `--no-live-fx` (pin to the static rate table instead of a
live lookup).

**Streamlit app:**

```
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```

**Watcher:**

```
.\.venv\Scripts\python.exe cash_watcher.py --once --borrower leasy
```

**One-time OAuth grants** (browser flows, tokens written to `.env`):
`scripts/google_oauth_setup.py` (Drive store), `google_oauth_setup_inbox.py`
(Drive inbox), `google_oauth_setup_gmail.py` (Gmail send).

## 9. The `redshift-api` sibling service

The loan-tape dropdown and watcher talk to a **separate** FastAPI service
(`redshift-api/`) that proxies read-only queries to Redshift Serverless. It
enforces SELECT-only + schema allowlist + row caps server-side and exposes
`/health`, `/health/db`, `/borrowers`, `/loan-tape`, and a general-purpose
`/query` (same SELECT-only + allowlist + row-cap guarantees, used by the Ask
(AI) tab's warehouse drill-down). It must be running (and, in production,
publicly reachable) for the dropdown/watcher to work; without it the app
degrades to file-upload-only rather than crashing.

## 10. Deployment

- **Streamlit Community Cloud** — push `verifications-automation` as its own
  repo → New app → main file `app/streamlit_app.py`. No secrets are needed for
  a first deploy; every optional integration (Redshift, Verifik, OCR) degrades
  gracefully without its env vars. After a code push, use **Reboot app** (a
  plain pull reruns the script but leaves already-imported submodules cached).
- **Watcher schedule** — `cash-watcher.yml` on a **self-hosted** GitHub Actions
  runner (the Redshift endpoint resolves to a private VPC IP, so a cloud runner
  cannot reach it). State + `out/` persist to Drive via `--drive-sync`.
- **`redshift-api`** — deployed to Railway
  (`redshift-api-lt-production.up.railway.app`); needs the Redshift workgroup
  to be publicly reachable (or a VPC/self-hosted approach) to work from the
  internet.

## 11. Testing & quality conventions

- `tests/` — plain-assert `pytest` suites, one per module (`test_extract.py`,
  `test_reconcile.py`, `test_anomaly.py`, `test_cash_watcher.py`, …). Real
  sample files in `samples/` are gitignored; tests that use them skip
  gracefully when absent.
- `streamlit_app.py` UI glue is validated via Streamlit `AppTest` + live
  Playwright browser passes rather than unit tests (a documented convention).
- Every change is verified with `ruff check .` + full `pytest` from the venv
  interpreter, and significant work is logged in `docs/PROGRESS.md`.

## 12. Known scope / open items

- **Statement-side cut-off-date synchronization is not built** (a deliberate
  scope decision — there's no real-time cadence to reliably know when
  statement data is "as-of").
- **Owner verification for Peru** needs Verifik's SUNARP/Full-ID contract
  confirmed; owner-based checks there are unavailable now.
- **MX / EC** vehicle registries remain mock-only until each live contract is
  confirmed.
- **Variance attribution on full-tape runs** — tape-derived "collections" are
  an estimate (`total_loan_amount` minus outstanding), so large
  tape-vs-statement variances can reflect a period/source mismatch rather than
  a real discrepancy; interpret totals accordingly.
