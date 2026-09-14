# Verifications Automation — User Guide

> **Who this is for:** end users of the verification tool — the verification
> team and anyone else who runs statement verifications, reviews the results,
> or signs them off. It explains **what** the tool does and **how to use it**,
> not how it is built (that is `docs/TECHNICAL_ARCHITECTURE.md`).

## 1. What the tool does

The Verifications Automation tool checks that the money reported in Lendable's
**loan tapes** matches the money that actually moved in **bank / mobile-money
statements**. It does this deterministically — the program computes every
number itself; it doesn't ask an AI to do arithmetic.

At a high level the tool:

1. **Ingests** the loan data (the "loan tape") and the statements (bank
   statements, mobile money, ledgers, cash maps).
2. **Computes its own totals** from the statements (collections, disbursements,
   cash balance) using rules that are fixed and version-controlled.
3. **Compares** those computed figures against the loan tape's reported figures.
4. **Flags** anything that doesn't line up (a "variance") or looks anomalous
   (round amounts, duplicate transactions, off-hours activity, etc.).
5. **Produces a working-paper report** and lets a human **review, approve, and
   sign off** each flagged item.

The numbers the tool derives from statements are **never treated as fact on
their own** — the tool tells you the confidence of each extraction. Anything
below the confidence floor is surfaced for a manual spot-check rather than
silently trusted.

## 2. Access and authentication

The app is a browser-based tool, hosted for the verification team. **Access is
limited** to the verification team and whoever they approve. If a new team
member or a new statement format needs access, raise it with the verification
team first.

The tool uses **Google Drive** in two ways behind the scenes:

- **Automatic processing** — statements you drop into a borrower's configured
  Drive sub-folder are picked up without manual upload (§6).
- **Runs and reports are saved** and written out to the run folder; in the
  scheduled flow the working paper is emailed to the configured recipients.

A per-user "sign in with Google to save and share your own runs" feature is
planned but **not yet part of this app** — do not rely on it today. If you need
to save or share results, use the working-paper report and the run output folder.

## 3. The layout: five tabs

The app has five tabs. Use them in this order for a normal verification:

| # | Tab | Purpose |
|---|-----|---------|
| 1 | **Run verification** | Upload sources, run the verification, view results and the transaction-level drill-down. |
| 2 | **Asset & vehicle verification** | Check vehicles/assets against a registry (live or from a file). |
| 3 | **Review & sign-off** | Review each flagged item; approve/reject and sign off. |
| 4 | **Audit log & hardening** | See the run history, lead times, and hardening record. |
| 5 | **Ask (AI)** | Ask natural-language questions about a run's figures. |

A **sidebar**, visible on every tab, lets you **load a past run** (pick it
from the dropdown and click "Load selected run") instead of starting a new
one, and shows a live **read-only thresholds panel** — the
collections/disbursement/cash variance limits, the forensic-route score, and
the PDF confidence floor currently in effect from `config.yaml`.

---

### Tab 1 — Run verification

This is the main workflow. It has two parts stacked on the same page.

**Part A — upload and run**

1. **Upload sources.** You can supply the loan data and the statements any of
   these ways:
   - **Loan tape (reported figures)** — upload a loan-tape file, **and/or** use
     the **"Loan tape from Redshift"** dropdown to pick a borrower and load
     their tape directly from the data warehouse. You can narrow what's
     pulled with a begin-date range and a max-rows cap, and once it loads you
     get a summary panel (loan count, principal, outstanding, collected,
     broken down by status/country/product) plus Period/Product/Country
     filters — these filters actually change what gets reconciled in step 2,
     not just what's displayed. From there you can also **download the
     loaded tape as a Google Sheet**.
   - **Bank statements** — upload the statement files (PDF, CSV, XLSX, or an
     image of a statement). Multiple statements are fine.
   - **Mobile money** — upload the statement files (CSV or XLSX only — PDF or
     image mobile-money statements aren't supported yet).
   - **Ledger** and **cash map** — optional extra sources. If you upload a
     cash map, you'll be asked to confirm which cash-map account each
     statement corresponds to; the tool then shows money-in/out and closing
     balance for that mapping.
2. **Run verification.** Click the button to run the pipeline. The tool:
   - extracts every transaction from each statement,
   - computes collections, disbursements, and cash totals (converting
     currencies to USD using live FX rates by default),
   - compares them to the loan tape's reported figures,
   - produces the exceptions list and the working-paper report.

3. **View results.** The Results section shows:
   - the **aggregates** (reported vs. computed side by side),
   - the **exceptions** (each variance/anomaly with the evidence that produced it),
   - the **forensic review queue** (the most serious items, at or above the
     high-severity threshold),
   - **mandatory fraud referrals** (a small set of patterns that are always
     routed for review, separate from the forensic queue),
   - any **warnings** (e.g. a currency that couldn't be converted, or a
     statement that fell below the confidence floor and needs a manual
     spot-check).

   Where the tool could not extract a figure, it says **"no data uploaded"**
   rather than showing a misleading `0.00`.

**Part B — detailed transaction matching (optional)**

Below the results there's a best-effort, row-level drill-down. Where the
verification answers *"do the totals line up?"*, transaction matching answers
*"which specific transaction has no counterpart on the other side?"*

- It **reuses** the files you already uploaded above — you don't upload twice.
- For each side, pick the **column** that actually carries a reference (e.g. a
  transaction or loan ID). A **matching options** panel lets you toggle case
  sensitivity, ignore spaces/special characters, and allow partial matches —
  these change which rows count as matched, so check them if the breakdown
  looks off.
- A **totals check** at the top compares the sum of any chosen amount column on
  each side and tells you whether they match.

> **Tip:** pick a source shape that matches your file. If a file is
> bank-statement-shaped but treated as a loan tape, the tool parses no rows;
> it will warn you rather than showing a wrong-looking result.

---

### Tab 2 — Asset & vehicle verification

This tab checks a vehicle or asset against a vehicle registry. It has two
modes (choose with the radio at the top), sharing one country selector:

- **Verify a reported asset register (bulk)** — upload the collateral register
  you want to check, then pick a **registry data source**: either a **live
  lookup** (each plate is checked against the registry in real time) or
  **upload a pre-run registry-check file** if the checks were already produced
  by a separate tool (e.g. the standalone Peru plate-checker). Either way the
  tool flags each asset as checked / not checked / failed / owner-mismatch,
  and findings flow into the **Review & sign-off** tab for approval, just
  like other exceptions.
- **Quick lookup / spot check** — a one-off plate check (single plate or a
  bulk list via Excel), with an optional "expected vehicle" column that the
  tool classifies as Match / Partial / Mismatch.

Some countries require an extra input alongside the plate (shown
automatically when relevant) before a live lookup can run.

The registry data comes from **Verifik** for the countries that are live
(Peru, Colombia, Chile, Argentina, Brazil). Where a country or owner field is
not confirmed, the tool does **not** guess — it leaves that field blank rather
than return a made-up answer.

> **Note:** for Peru, the live endpoint returns brand/model/year but **no
> owner name**, so owner-based checks are not possible there yet. This is a
> real limitation of the registry API, not a bug.

---

### Tab 3 — Review & sign-off

This is where the human review happens. Every exception from a run lands here.

- Each item can be **approved** or **rejected**, with the reviewer's name and a
  note.
- Once reviewed, a single **sign-off** by senior risk closes the run.
- Every approve/reject/sign-off action is **logged** to the audit trail — it is
  never overwritten.

Nothing you do here is a blocker in the automatic flow; review is a downstream
step that happens **after** a run is produced.

---

### Tab 4 — Audit log & hardening

Mostly a read-only view of how the system has been behaving:

- **Run log** — every run, when it started, and its outcome.
- **Lead time** — how long each run took from start to sign-off.
- **Last verification per borrower** — the most recent run per borrower,
  oldest first, so overdue borrowers sit at the top.
- **Hardening record** — feedback and any rule promotions that have been
  applied over time. A **"Record a hardening promotion"** form lets you log a
  new pattern/change/effect directly — this is the one write action on the
  tab.

This is the place to check whether the data is up to date and which borrowers
are covered.

---

### Tab 5 — Ask (AI)

Ask a question in plain English about the currently active run, for example
*"what's driving the collections variance?"*. The answer is **grounded on the
run's own figures and exceptions** — it treats the aggregates as given and
never recomputes them.

- You can optionally tick **"Allow warehouse drill-down"** to let the AI run
  **read-only** queries against the data warehouse to support its answer. This
  is only available if the warehouse connection is configured.
- If no AI provider is configured, this tab tells you so instead of failing.

---

## 4. If your statement isn't recognised

Statements are parsed assuming a table-like transaction layout (date, amount,
description). The parser handles several real layouts, including:

- single-date statements,
- two-column-date statements with a running balance (common in Peru),
- ISO-date statements with a running balance,
- PDFs with a numbered `# DATE NARRATION DEBIT CREDIT BALANCE` table,
- **scanned / image-only** statements (via OCR when available),
- statements in **Spanish** (labels like `SOLES` / `DOLARES` are understood).

If a file can't be parsed confidently, the tool **does not silently return
nothing** — it flags the record for a **manual spot-check** so a human reviews
it. You can upload a brand-new statement format you've never seen before; if it
doesn't parse, it's surfaced for review rather than trusted.

---

## 5. Working-paper report and email

Each completed run produces a **working paper** (the report of what was
checked, the aggregates, and the exceptions). In the scheduled/automatic flow
the working paper is emailed to the configured recipients as a **Word
(.docx) document**, alongside the raw JSON.

Scheduled runs also support a separate, **hash-verified sign-off** at the
command line (distinct from the in-app review in Tab 3) — it records who
signed off and detects if the report was edited or regenerated afterward.
Ask the verification team's technical contact if you need this for an
automated run.

If a run is re-run, the tool does **not** re-download or re-process the same
statement — each file is fingerprinted so it is handled exactly once.

---

## 6. Statement drop-folder (automatic processing)

For borrowers set up with an inbox, you can drop a statement into that
borrower's **Google Drive sub-folder** and it will be picked up automatically —
no manual upload needed. When new statements appear, the tool:

1. notices the new file,
2. runs the verification for that borrower,
3. generates the working paper and (if enabled) emails it.

A "random" folder is never scanned — only the **configured sub-folder per
borrower**. If you don't use the drop-folder, the manual route in Tab 1 always
still works. The drop-folder and the manual route are the same pipeline, so the
results are identical either way.

---

## 7. Quick reference — common tasks

| Task | What to do |
|------|------------|
| Verify one borrower's statements | Tab 1 → load the loan tape (Redshift dropdown or file) → upload the statements → Run → review in Tab 3. |
| Check a vehicle plate quickly | Tab 2 → pick country → "Quick lookup" → enter the plate. |
| Approve/sign off flagged items | Tab 3 → review each item, then sign off. |
| Check whether data is up to date | Tab 4 → Run log / Last verification per borrower. |
| Ask a question about a run | Tab 5 → type the question. |
| Have statements processed automatically | Drop the file into the borrower's configured Drive sub-folder. |

## 8. Getting help / raising an issue

For anything not covered here — a new statement format, an access request, a
report that doesn't look right, or a question about the numbers — contact the
verification team. This is an internal tool; access and statement-handling
decisions are made by the verification team, not by the tool itself.
