#!/bin/bash
# One scout pass: searches for postings only. It never applies or contacts anyone.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
mkdir -p logs scout/reports
LOG="$DIR/logs/scout-$(date +%F).log"
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
KEEP_AWAKE=""; command -v caffeinate >/dev/null && KEEP_AWAKE="caffeinate -i"
ALLOWED=(
  "Bash(./fetch_jobs.py find *)" "Bash(./fetch_jobs.py add *)" "Bash(./fetch_jobs.py scout-rank *)" "Bash(./fetch_jobs.py list *)"
  "Bash(curl -s *)" "Bash(curl -sL *)" "WebSearch" "WebFetch"
  "Read($DIR/**)" "Write($DIR/scout/**)" "Edit($DIR/scout/**)"
)
{
  echo "=== $(date) ==="
  $KEEP_AWAKE "$CLAUDE" -p "Run one scout pass now, following your instructions. Nobody is at the computer: never ask questions." \
    --agent internship-scout --permission-mode dontAsk --allowedTools "${ALLOWED[@]}"
  echo "=== exit $? at $(date) ==="
} >> "$LOG" 2>&1
