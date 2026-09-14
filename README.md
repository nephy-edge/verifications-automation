# Verifications Automation — Lendable Risk workflow

This project implements the "Verifications Automation" workflow recorded in
`Overview_-Verifications.html`. It is split into one folder per build phase so
each phase is reviewable and independently testable.

## Folder layout

```
verifications-automation/
├── phase0_foundations/            # config, logging, models, schema
├── phase1_ingestion_parsing/      # multi-format ingest + PDF extraction
├── phase2_verification_engine/    # deterministic aggregation + reconciliation
├── phase3_anomaly_reporting/      # anomaly detection, ranking, reports
├── phase4_human_review/           # review + approval workflow
├── phase5_feedback_hardening/     # feedback loop + rule hardening
├── app/streamlit_app.py           # review dashboard (upload -> run -> review/sign-off)
├── .streamlit/config.toml         # native Streamlit theme fallback
├── runner.py                      # end-to-end CLI entrypoint
├── config.yaml                    # deterministic thresholds (single source of truth)
├── requirements.txt
├── .env.example
├── docs/PROGRESS.md
└── tests/
```

Note: phase directories use underscores (valid Python package names) so each
phase is directly importable (e.g. `from phase2_verification_engine import reconcile`).

## Design rules (from the workflow record)

- The LLM never does arithmetic; code never invents facts or judgement.
- Deterministic-first: SQL/rules/arithmetic are the spine; LLM reserved for
  genuine interpretation (PDF parsing, anomaly narrative) and human for sign-off.
- Every run and every human review action is logged to `verifications.log.jsonl`.
- Thresholds live in `config.yaml` and are version-controlled.

## Layout of phases vs. the workflow steps

- Phase 0  = A3 steps (infra) + B3 thresholds + B4 model config
- Phase 1  = A3 steps 1–2 (ingest + PDF parse) + A5 methodology
- Phase 2  = A3 steps 3–4 (calculate + reconcile + asset-existence check) — all deterministic
- Phase 3  = A3 steps 5–6 (anomaly + report) 
- Phase 4  = A3 steps 7–8 (human review + sign-off)
- Phase 5  = B5 feedback/hardening loop

## Environment

Dependencies install only into this project’s `.venv` (never global):

```
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` installs the runtime deps plus the developer tooling
(`ruff`, `pytest`) that runs the quality gate. `requirements.txt` alone is the
runtime subset — fine for a bare deployment, but it will not give you the lint
and test commands below.

Run the quality gate (closed-loop verification, per AGENTS.md):

```
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest
```

Run end-to-end with the venv interpreter:

```
.\.venv\Scripts\python.exe runner.py --config config.yaml --input <tape> <statements...> --out out/
```

Or use the review dashboard:

```
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```

Upload sources, run the pipeline, then review/approve/reject and sign off
exceptions from the browser — every action lands in the same
`verifications.log.jsonl` the CLI writes to. Design reuses the workspace's
`streamlit_design` skill tokens for visual parity with `Bank Access`.

Five tabs total. Two are standalone, on-demand tools — separate from the
Run/Review/Audit pipeline by design, each with its own uploaders and its own
result, not merged into `run.exceptions`:
- **Asset & vehicle verification** — `phase2_verification_engine/assets.py` +
  `phase1_ingestion_parsing/vehicle_verify.py`. Two modes sharing one country
  selector and one registry client:
  - *Verify a reported asset register* (bulk, owner-focused): compares a
    collateral register against a registry check per plate, and raises
    `ExceptionItem`s into the Review/Audit sign-off queue. The registry side
    defaults to a **live lookup** via `vehicle_verify.make_client()` (mocked
    until `VERIFIK_TOKEN` is set) — the same lookup that used to require a
    manually pre-run, separately uploaded registry-check file; that upload
    path is kept as a fallback for a check already run elsewhere.
  - *Quick lookup / spot check*: an ad hoc, register-free plate lookup (single
    or bulk-via-Excel), with an optional expected-vehicle column classified
    Match/Partial/Mismatch — a CAPTCHA-free alternative to
    `vehicle_plate_peru/checker.py`'s browser scraper, for the case where you
    just want to check a few plates, not run a formal audit.
- **Transaction matching** — `phase2_verification_engine/transaction_match.py`,
  a matched/unmatched comparison between a reported source and an
  independent one, complementing (not replacing) the deterministic
  aggregate reconciliation in the Run tab.

See docs/PROGRESS.md's 2026-08-25 entries for why these are separate tabs rather
than folded into the Run tab's upload flow, and the 2026-08-26 entry for why
asset existence and vehicle verification were later merged into one tab.

Scanned/image-only PDF statements fall back to OCR
(`phase1_ingestion_parsing/ocr.py`) when the `tesseract` binary is installed
(see `.env.example`'s `TESSERACT_CMD`); without it, they degrade to the same
manual-spot-check placeholder as before OCR existed. `packages.txt` installs
`tesseract-ocr` automatically on Streamlit Community Cloud, so OCR is live
there even though it isn't on a bare local machine.

## Deployment (Streamlit Community Cloud)

1. Push this folder as its own repo (see docs/PROGRESS.md's deploy entry for what
   was deliberately excluded — real sample files under `samples/`).
2. On share.streamlit.io: New app → pick the repo → main file path
   `app/streamlit_app.py`.
3. No secrets are required for a first deploy — every optional integration
   degrades gracefully without them:
   - No `REDSHIFT_API_KEY`/`REDSHIFT_API_URL` → the Redshift loan-tape
     dropdown shows a connection error; file upload still works fully.
   - No `VERIFIK_TOKEN` → Vehicle verification runs on `MockVehicleClient`
     (already the default — nothing to configure either way until
     `VerifikClient` is wired live).
   - No `TESSERACT_CMD` → OCR still works via `packages.txt`'s system
     install; only needed to point at a non-standard `tesseract` binary.
4. To enable the Redshift dropdown against a real warehouse from a deployed
   app, `redshift-api` needs its own public deployment too — it's a separate
   FastAPI service, not something Streamlit Cloud can reach at
   `127.0.0.1:8001` once this app is no longer running next to it locally.

See `docs/PROGRESS.md` for the live task log.
