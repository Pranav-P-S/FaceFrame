#!/usr/bin/env bash
# Single-purpose runner so background tasks never need compound commands.
# Usage: run_stress_e2e.sh <library-dir> <log-name> [extra e2e args...]
set -u
LIB="$1"; shift
LOGNAME="$1"; shift
LOG="/c/Projects/FaceFrame/test-data/stress-logs/${LOGNAME}"
cd /c/Projects/FaceFrame
./venv/Scripts/python.exe -u scripts/stress_e2e.py --lib "$LIB" "$@" > "$LOG" 2>&1
echo "EXIT=$?" >> "$LOG"
