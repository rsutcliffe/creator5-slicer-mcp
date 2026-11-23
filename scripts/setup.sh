#!/bin/sh
# One command to install or update the server and connect it to Claude.
#
#   ./scripts/setup.sh
#
# Safe to run again at any time. It updates the code, installs it into .venv,
# slices a test cube with your real slicer, and only if that passes registers
# the server with Claude Desktop and Claude Code.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$(pwd)
LOG="$ROOT/setup_report.txt"
step() { printf '\n== %s\n' "$1"; }

{
step "Updating code"
if [ -d .git ]; then git pull --ff-only || echo "(could not update; continuing with the current copy)"; fi

step "Installing into .venv"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv || exit 1; fi
.venv/bin/python -m pip install --quiet -e ".[step]" || exit 1
echo "installed"

step "Installing the print check (text-to-cad's cad command), with its usage stats and self-update off"
if command -v uv >/dev/null 2>&1; then
    if uv tool install cad >/dev/null 2>&1 || command -v cad >/dev/null 2>&1; then
        PATH="$HOME/.local/bin:$PATH"
        cad telemetry off >/dev/null 2>&1
        cad update auto off >/dev/null 2>&1
        cad --version
    else
        echo "(could not install cad; models are sliced without the print check)"
    fi
else
    echo "(uv is not installed, so cad was skipped; models are sliced without the print check. Get uv with: brew install uv)"
fi

step "Checking the slicer by slicing a small cube to a file (nothing is sent to the printer)"
if .venv/bin/python scripts/smoke_test.py; then
    SMOKE=ok
else
    SMOKE=failed
fi

if [ "$SMOKE" = ok ]; then
    step "Registering with Claude Desktop"
    .venv/bin/python scripts/install_claude_desktop.py
    if command -v claude >/dev/null 2>&1; then
        step "Registering with Claude Code"
        claude mcp remove creator5-slicer --scope user >/dev/null 2>&1
        claude mcp add --scope user creator5-slicer -- "$ROOT/.venv/bin/creator5-slicer-mcp"
    fi
    step "RESULT: ready. Quit and reopen Claude Desktop to load the server."
else
    step "RESULT: the test slice failed, so nothing was registered. Share setup_report.txt to get it fixed."
fi
} 2>&1 | tee "$LOG"
printf '\nFull report saved to %s\n' "$LOG"
