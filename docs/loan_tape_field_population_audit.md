# Loan-tape field-population audit (2026-09-08)

Follow-up to `loan_tape_column_survey.md` (2026-09-03). That survey listed
per-field *candidate* columns without full context; this pass went through
every borrower's **complete** column list (same survey doc, "Full column
list per borrower" section) and resolved as many as could be matched with
real evidence, then verified the result against **live data** from the
running redshift-api service (not just column names — actual sampled row
values).

## What changed

`config.yaml`'s `loan_tape_columns.overrides` went from 4 borrowers to
**41 of 46**. The 5 needing no override (already match the default column
names exactly): `f88`, `prestamype`, `r5`, `solvento`, `amartha2`.

Rules followed throughout (same discipline as the original survey):

- A source column is never pointed at by two different semantic fields —
  that would double-count it in aggregation math. Where a borrower only had
  one plausible amount column for two concepts (e.g. `principal_amount` and
  `total_loan_amount`), only the more literal one got mapped; the other
  stays unmapped rather than guessed.
- A field with no real equivalent for a borrower is left unmapped, same as
  before — that's the honest answer for products that don't carry the
  concept at all (e.g. `mkopa` and `dlight` are pay-as-you-go financing, not
  amortizing loans — no interest/fee/penalty-outstanding data exists for
  them, not a mapping gap).

## Regressions found and fixed

- **`advance`**: the 2026-09-03 fix that switched the *default* `fee_outstanding`/
  `penalty_outstanding` from plural to singular (correct for `leasy` et al.)
  broke `advance`, whose real columns are plural (`fees_outstanding`/
  `penalties_outstanding`). Same issue found in `first_digital_finance_corporation`.
  Both now have an explicit override back to the plural names. **Confirmed live**:
  both now return real values on every sampled row.
- **`first_digital_finance_corporation` status**: mapped to `loan_status` first
  (looked like the obvious candidate), but a live 200-row sample showed it's
  `NULL` on every row. Its sibling column `loan_status_id` is fully populated
  but is a numeric code (`300`, `600`, ...) with no legend in this table.
  Mapped to `loan_status_id` instead — a coded value a reviewer can look up
  beats a permanently blank tile, but **the code meanings should be confirmed
  with whoever owns this borrower's data** before relying on it for reporting.

## Still needs a business call (left unmapped, not guessed)

- **`amartha` / `amartha2` / `amartha_full` / `amartha_full_portfolio` /
  `amartha_secured`** — `principal_amount` and `principal_outstanding`:
  every amartha table carries both a `_lendable` and a `_loan` suffix variant
  (Lendable's own exposure vs. the full underlying loan). Which one belongs
  in "the" principal tile is a real business decision, not a naming question.
- **`koinworks`** — `loan_id` (`id` vs `loan_code`, unclear which is the
  canonical business identifier); `principal_amount` (only `funding_amount`
  exists, unclear if that's the same concept).
- **`moladin`** — `loan_id` (`active_loan_id` vs `loan_submission_id`).
- **`lhoopa`** — `status` (`property_status` vs `construction_status`, two
  distinct concepts).
- **`sary`** — `interest_outstanding` / `penalty_outstanding`: candidates
  `moratetaxincl` / `totalmargintaxincl` use unclear local terminology.
- **`validus_id` / `validus_id_full`** — `principal_amount` (`total_loan` may
  already include interest) and `interest_outstanding` (`actual_interest_amount`
  is ambiguous between total and remaining-outstanding).
- **`validus_id_lendable` / `validus_id_secured`** — `interest_outstanding`
  (`interest_amount`, same total-vs-outstanding ambiguity).
- **`credismart`** — `penalty_outstanding`: no separate column; likely folded
  into `other_fees_and_charges_still_outstanding` (already used for `fee_outstanding`).

## Live data-completeness findings (not mapping bugs)

These fields resolve to the *correct* real column, confirmed by name, but
came back `NULL` on every row in a 200-row live sample — worth a data-pipeline
check with whoever owns these borrowers' dbt sources, since the column
existing but never populated could mean it genuinely never applies, or that
upstream stopped filling it in:

- `leasy.penalty_outstanding` — 0/200 populated
- `khazna.days_past_due` — 0/200 populated
- `prestamype.interest_outstanding`, `fee_outstanding`, `penalty_outstanding` — 0/200 each

By contrast, `moladin.days_past_due` (4/200) and `f88.closure_date` (3/200)
looked similarly sparse at first but turned out fine at a larger sample —
expected behavior for fields that are only non-null for a small subset of
rows (delinquent loans / already-closed loans), not a gap.

## Operational note (unrelated to config) -- UPDATED, confirmed persistent

`mkopa`, `amartha2`, `amartha_full`, `amartha_full_portfolio`,
`validus_id_full`, and `validus_id_lendable` return **0 rows** from
`/loan-tape` with no date filter applied -- table exists (listed in
`/borrowers`, query succeeds), just no rows in the underlying S3/Spectrum
data right now. Re-checked ~30 minutes after the first pass: still 0 rows on
all 6, consistently -- this is **not transient**, unlike first assumed.
Not a config or mapping problem (nothing here touches the query itself),
but a real gap: any of these 6 borrowers will show a fully-empty loan tape
in the app right now. Worth flagging to whoever owns the dbt_source
pipeline for these tables.

## Verification method

Live-queried every borrower via the running redshift-api service (25-row and,
for flagged fields, 200-row samples), resolved each field through
`LoanTapeColumnsConfig.resolve()` exactly as the app does, and checked real
row values (not just that the column exists) for at least one non-null
sample. Full test suite (176 tests) still green after the config change —
this was a data-only edit, no code paths changed.

## 2026-09-09 — Computation correctness pass (not just column mapping)

Follow-up: ran every borrower's real, fetched tape through the ACTUAL app
function (`streamlit_app._loan_tape_summary`, imported directly — not a
reimplementation) and checked the *figures* it produces for sanity (no
crashes, no negative totals, outstanding never wildly exceeding the loan
amount, delinquency/status/country breakdowns summing back to the loan
count). This caught two real, previously-undetected bugs that field-mapping
correctness alone couldn't surface — both hit the real verification pipeline
(`ingest.py`), not just the preview tiles:

1. **Comma-formatted money strings** — `exitus.principal` stores values like
   `"6,959,813.28"`. `float()` and `pd.to_numeric` both reject the embedded
   comma outright and silently return `0.0` — same bug class as the
   fee/penalty singular-vs-plural mismatch fixed 2026-09-03, just triggered
   by formatting instead of a wrong column name. Fixed in both
   `ingest.py::_parse_float` and `streamlit_app.py::_loan_tape_summary`'s
   `_num()`. **A second bug surfaced while fixing the first**: the initial
   fix gated the comma-strip on `series.dtype == object`, but pandas can
   infer a dedicated string dtype (`dtype('str')`) for an all-string column
   instead of falling back to `object` — the check silently missed exactly
   the case it was written for. Re-gated on `not pd.api.types.is_numeric_dtype(series)`.
2. **Negative-sign accounting convention, confirmed per-borrower (not
   assumed from a sibling)**: `lendmn` and `lendmn_revolving`'s `principal`,
   and `autocheck__ci`/`autocheck__ug`'s `principal_out`/`interest_out`/
   `fees_out`/`penalty_out`, are negative on effectively every sampled row —
   not an occasional anomaly. `lendmn_micro` (same borrower family as
   `lendmn`) was checked separately and is normally signed — confirming the
   fix had to be per-borrower, not per-family. Initial instinct was to floor
   negative components at 0 (safe for a rare/sparse negative, e.g. a genuine
   credit), but that would have silently zeroed out **real debt** for these
   four borrowers, which is worse than the original bug. Added
   `loan_tape_columns.negative_sign_borrowers` to `config.yaml` (currently
   `lendmn`, `lendmn_revolving`, `autocheck__ci`, `autocheck__ug`) —
   `normalize_loan_tape_row()` and `_loan_tape_summary()` both take a
   `negative_sign` flag now: `abs()` for listed borrowers, floor-at-0
   (unchanged default) for everyone else.

**Verified live, before and after**: `exitus.principal_amount` went from a
near-zero sum (comma-parsing dropping almost everything) to a sane
`$1.98B`/200 rows; `lendmn`/`lendmn_revolving`/`autocheck__ci`/`autocheck__ug`
went from trillion/billion-scale *negative* totals to sane positive figures
with `principal_outstanding < principal_amount` as expected for a partially
repaid portfolio. Re-ran the full 46-borrower sweep afterward: **zero**
negative-total or exceeds-by->50% findings remained (down from 6). The only
remaining findings are the same 6 persistently-empty tables above, plus
transient fetch timeouts/500s on a handful of borrowers during heavy
back-to-back full-table sweeps — confirmed NOT a code issue by re-checking
each individually with a light, spaced-out query (all returned clean sane
figures). 8 new tests added (`test_ingest.py`, `test_config.py`), full suite
green (224 tests).

**Also found and fixed along the way (infrastructure, not app logic)**:
`redshift-api` was intermittently dying mid-session because it was running
as a child process of the Claude Code session itself — any cleanup of that
parent process tree silently killed it (same root cause diagnosed earlier
for the Streamlit process). Restarted fully detached (Windows `Start-Process`)
so it no longer depends on the session's own process lifetime.
