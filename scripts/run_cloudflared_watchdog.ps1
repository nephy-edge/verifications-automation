# Launcher for the Windows Scheduled Task registered by this project's setup
# (see docs/cloud-runner-setup.md) -- runs cloudflared_watchdog.py with the
# right working directory (so its relative google_oauth_token.json / .env
# lookups resolve) and appends its output to a log file, since a Scheduled
# Task action has no shell redirection of its own.

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$script = Join-Path $ProjectRoot "scripts\cloudflared_watchdog.py"
$log = Join-Path $ProjectRoot "cloudflared_watchdog.log"

"[$(Get-Date -Format o)] launcher starting" | Add-Content -Path $log -Encoding utf8

& $python $script *>> $log
