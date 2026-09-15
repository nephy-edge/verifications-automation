"""Keep a cloudflared quick tunnel to the local redshift-api alive, and
publish its current public URL to Drive so the Streamlit Community Cloud
deployment can find it without a manual secret update.

Why this exists: the Redshift Serverless workgroup currently resolves only
to a private VPC IP (see docs/PROGRESS.md's 2026-09-10/11 entries), so the
Streamlit Cloud app's Redshift dropdown has no way to reach `redshift-api`
running on this machine. A cloudflared *quick* tunnel (no Cloudflare account
needed) gives it a public HTTPS URL, but that URL is random and changes every
time the tunnel restarts -- Streamlit Cloud has no API for updating its own
secrets, so a rotating URL can't be wired in there directly.

Instead: this script supervises the tunnel process, extracts the current
`https://*.trycloudflare.com` URL from its output whenever it (re)connects,
and publishes it via `phase0_foundations.drive_store.push_tunnel_url`. The
app reads it back with `fetch_tunnel_url` (cached, short TTL) instead of a
static `REDSHIFT_API_URL` -- see app/streamlit_app.py's
`_resolve_redshift_api_url`. No one needs to touch Streamlit secrets again
after the one-time `GOOGLE_OAUTH_TOKEN_JSON` secret is set (it's the same
Drive credential gsheet_export.py already uses).

Usage:
    python scripts/cloudflared_watchdog.py [--target http://127.0.0.1:8001]
                                            [--cloudflared cloudflared]

Runs until interrupted (Ctrl+C). For it to survive logoff/reboot the way the
self-hosted GitHub Actions runner does, register it as a Windows Scheduled
Task or Service -- this script itself is just the supervisor loop.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from phase0_foundations.drive_store import DriveStoreError, push_tunnel_url  # noqa: E402

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_FAILURE_RE = re.compile(r"Retrying connection|failed to serve tunnel connection")
_RESTART_BACKOFF_SECONDS = 10
# cloudflared can get stuck retrying a broken QUIC session forever without
# ever exiting (observed live: "control stream encountered a failure" /
# "Retrying connection in up to 1m4s", looping indefinitely) -- the tunnel is
# dead but the process never does, so "restart when the process exits" alone
# never fires. Force-kill and restart after this many consecutive failure
# lines with no fresh URL in between, so a new (working) quick tunnel gets
# minted instead of silently leaving a dead one published.
_MAX_CONSECUTIVE_FAILURES = 4


def _publish(url: str) -> bool:
    try:
        push_tunnel_url(url)
        print(f"[watchdog] published {url} to Drive", flush=True)
        return True
    except DriveStoreError as exc:
        print(f"[watchdog] could not publish to Drive (will retry next rotation): {exc}", flush=True)
        return False


def _run_one_tunnel(cloudflared: str, target: str) -> None:
    """Start one cloudflared quick tunnel and stream its output, publishing
    the URL the moment it's assigned. Returns when the process exits (crash,
    network blip, or Cloudflare closing the session) so the caller restarts it."""
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, no user input
        [cloudflared, "tunnel", "--url", target],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    published_url: str | None = None
    consecutive_failures = 0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            match = _URL_RE.search(line)
            if match and match.group(0) != published_url:
                published_url = match.group(0)
                consecutive_failures = 0
                _publish(published_url)
                continue
            if _FAILURE_RE.search(line):
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    print(f"[watchdog] {consecutive_failures} consecutive reconnect "
                          "failures with no working tunnel -- forcing a restart "
                          "instead of waiting for cloudflared to exit on its own", flush=True)
                    return
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="http://127.0.0.1:8001",
                         help="Local redshift-api URL to tunnel (default: %(default)s)")
    parser.add_argument("--cloudflared", default="cloudflared",
                         help="cloudflared executable name/path (default: %(default)s)")
    args = parser.parse_args()

    print(f"[watchdog] supervising a cloudflared quick tunnel -> {args.target}", flush=True)
    while True:
        started = time.monotonic()
        try:
            _run_one_tunnel(args.cloudflared, args.target)
        except FileNotFoundError:
            print(f"[watchdog] '{args.cloudflared}' not found on PATH -- aborting", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("[watchdog] stopped", flush=True)
            return 0
        elapsed = time.monotonic() - started
        print(f"[watchdog] tunnel exited after {elapsed:.0f}s -- restarting "
              f"in {_RESTART_BACKOFF_SECONDS}s (new URL expected)", flush=True)
        time.sleep(_RESTART_BACKOFF_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
