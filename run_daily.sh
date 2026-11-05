#!/bin/bash
# Unattended daily run of the agent. install_schedule.sh sets it up; run it by hand to test.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
mkdir -p logs
LOG="$DIR/logs/$(date +%F).log"
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
KEEP_AWAKE=""; command -v caffeinate >/dev/null && KEEP_AWAKE="caffeinate -i"

PROMPT="/internship-agent
This is the unattended daily run. Nobody is at the computer: never wait for input or ask questions.
Do all browser work in a new window of your own, never in the user's existing tabs, and close your tabs when done.
Anything that needs the user is marked needs_user with a note saying exactly what. All hard rules in the skill apply.
If the browser tools are unavailable, skip anything that needs the browser and still do the fetch and email steps.
Finish with a one-line summary."

ALLOWED=(
  "Bash(./fetch_jobs.py *)" "Bash(./resume-builder/build_resume.py *)" "Bash(./dashboard/build_site.py)"
  "Read(/$DIR/**)" "Edit(/$DIR/applications/**)"
  "Skill" "ToolSearch" "WebSearch" "Agent"
  "mcp__claude_ai_Gmail__search_threads" "mcp__claude_ai_Gmail__get_thread" "mcp__claude-in-chrome"
)
{
  echo "=== $(date) ==="
  $KEEP_AWAKE "$CLAUDE" -p "$PROMPT" --chrome --permission-mode dontAsk --allowedTools "${ALLOWED[@]}"
  echo "=== exit $? at $(date) ==="
  ./dashboard/build_site.py
} >> "$LOG" 2>&1
