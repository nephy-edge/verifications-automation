# Cloud runner setup — scheduled loan-tape watcher on GitHub Actions

The SOP 1 watcher (`cash_watcher.py`) scheduled via GitHub Actions on a hosted
(ephemeral) runner. Two things make this different from a local Task Scheduler
run:

1. **The redshift-api must be publicly reachable** — a cloud runner cannot see
   `127.0.0.1:8001` on your machine.
2. **The runner's filesystem is wiped every run** — the watcher's state
   (`out/cash_watcher_state.json`) and retained statements must live somewhere
   persistent. We sync them to a Google Drive app folder via `--drive-sync`.

```
GitHub Actions (every 10 min, hosted runner)
   │  pull state + statements from Drive app folder
   ▼
cash_watcher.py --drive-sync --once
   │  fetch full tape from redshift-api (public URL)
   │  read new statements from Drive drop-folder
   │  reconcile → report (.docx/.json/.md) → email via Gmail API
   ▼
   push state + new statements + reports back to Drive
```

## Step 1 — deploy the redshift-api to a reachable host

The `redshift-api` (sibling folder) is a plain FastAPI app that connects to the
Redshift Serverless public endpoint. Recommended hosts:

- **Railway (recommended, easiest)** — connect it to a Git repo or `railway up`
  from the `redshift-api/` folder. Always-on; env vars; gives you an HTTPS URL.
  ~$5/month. Railway auto-detects the FastAPI app (`uvicorn app.main:app`).
- **Fly.io (cheapest always-on)** — `fly launch` in `redshift-api/` (~$2–5/mo),
  set env vars with `fly secrets set`.
- **Avoid Render's free tier** for this — free web services sleep after ~15 min
  idle, so a scheduled poll would wake it every time (slow, flaky).

Env vars to set on the host (same values as the local `redshift-api/.env`):

```
DATABASE_URL=<the Redshift Serverless connection string>
API_KEY=<your API key>
MAX_RESULTS_LIMIT=2000000
DEFAULT_RESULTS_LIMIT=100
QUERY_TIMEOUT_SECONDS=120
ALLOWED_SCHEMAS=dev_,uat_,prd_ana__,dbt_source
LOAN_TAPE_SCHEMA=dbt_source
```

Notes:
- Redshift's public endpoint is reachable from the internet, so no VPC peering
  is needed; the workgroup must be running when the watcher polls (Serverless
  auto-resume on connect; a paused/resuming workgroup was the cause of the
  earlier "HTTP Error 500" — it recovers on its own).
- After deploy, verify `GET <host>/health` → `{"status":"ok","configured":true}`
  and `GET <host>/health/db` → `{"database":"ok"}` (passing `X-API-Key`).

## Step 2 — collect the GitHub secret values

| Secret | Value |
|---|---|
| `REDSHIFT_API_URL` | `https://<deployed-host>` (no trailing slash) |
| `REDSHIFT_API_KEY` | the API key from `redshift-api/.env` |
| `GOOGLE_OAUTH_CLIENT_JSON` | the full JSON contents of `client_secret.json` (from `GOOGLE_OAUTH_CLIENT_JSON` in `.env`) |
| `GOOGLE_OAUTH_TOKEN_JSON` | the full JSON contents of `google_oauth_token.json` (drive.file — Sheets/state sync) |
| `GOOGLE_OAUTH_INBOX_TOKEN_JSON` | the full JSON contents of `google_oauth_inbox_token.json` (drive.readonly — statement drop-folder) |
| `GOOGLE_OAUTH_GMAIL_TOKEN_JSON` | the full JSON contents of `google_oauth_gmail_token.json` (gmail.send — report email) |

The workflow writes each JSON secret back to a file, so the `.env` paths resolve
as they do locally.

## Step 3 — add the secrets to GitHub

`gh secret set REDSHIFT_API_URL --repo nephy-edge/verifications-automation` etc.
(For the multi-line JSON secrets: `gh secret set NAME --body "@path/to/file"` or
paste in the UI's "Actions → Secrets" page.)

## Step 4 — enable the workflow

Push `.github/workflows/cash-watcher.yml` to the deployed repo, then either wait
for the `*/10` cron or trigger once manually: **Actions → cash-watcher → Run
workflow**. First run:
- pulls an empty Drive state (creates the `cash-watcher-state` folder),
- fetches the tape for the configured borrowers, runs, emails the .docx report,
- pushes state + reports back to Drive.

## Operational notes

- **Borrower scope**: config.yaml `sop1.watcher.borrowers: []` means "watch all
  46 borrowers", each a full-tape fetch per poll — heavy for a 10-min cron.
  For the scheduled cloud run, scope it (e.g. `borrowers: [leasy]`) or raise the
  interval.
- **Workgroup availability**: if the Serverless workgroup is down, the run
  degrades to "no changes" (fetch error, logged), state is preserved, and the
  next run retries — same resilience as locally.
- **Email + Drive** must be authorized for the same Google account as locally
  (they already are, via the OAuth files you grant once).
- **Audit trail**: reports + retained statements are both in Drive (via sync)
  and in the workflow's `cash-watcher-out` artifact (30-day retention).
