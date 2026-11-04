#!/bin/bash
# Schedules the daily run (and optionally the scout) on this computer.
#   ./install_schedule.sh              daily run at 06:45
#   ./install_schedule.sh 07:30        daily run at 07:30
#   ./install_schedule.sh 07:30 scout  also run the scout at 05:30 and 18:30
#   ./install_schedule.sh remove       removes everything this script installed
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
TIME="${1:-06:45}"
LABEL=com.internship-agent
if [ "$(uname)" != "Darwin" ]; then
  if [ "$TIME" = remove ]; then crontab -l 2>/dev/null | grep -v "$DIR/run_" | crontab - ; echo "removed"; exit 0; fi
  H="${TIME%%:*}"; M="${TIME##*:}"
  { crontab -l 2>/dev/null | grep -v "$DIR/run_" || true
    echo "$M $H * * * $DIR/run_daily.sh"
    if [ "${2:-}" = scout ]; then echo "30 5,18 * * * $DIR/run_scout.sh"; fi; } | crontab -
  echo "cron installed. Check it with: crontab -l"; exit 0
fi
AGENTS="$HOME/Library/LaunchAgents"
if [ "$TIME" = remove ]; then
  for n in daily scout; do launchctl unload "$AGENTS/$LABEL.$n.plist" 2>/dev/null || true; rm -f "$AGENTS/$LABEL.$n.plist"; done
  echo "removed"; exit 0
fi
plist() {  # plist <name> <script> <calendar xml>
  cat > "$AGENTS/$LABEL.$1.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL.$1</string>
  <key>ProgramArguments</key><array><string>$DIR/$2</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key>$3
  <key>StandardOutPath</key><string>$DIR/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.err</string>
</dict></plist>
PL
  launchctl unload "$AGENTS/$LABEL.$1.plist" 2>/dev/null || true
  launchctl load "$AGENTS/$LABEL.$1.plist"
}
cal() { echo "<dict><key>Hour</key><integer>$((10#$1))</integer><key>Minute</key><integer>$((10#$2))</integer></dict>"; }
mkdir -p "$AGENTS"
plist daily run_daily.sh "$(cal "${TIME%%:*}" "${TIME##*:}")"
echo "Daily run scheduled at $TIME. Your Mac must be awake and logged in, with Chrome running for browser steps."
if [ "${2:-}" = scout ]; then plist scout run_scout.sh "<array>$(cal 5 30)$(cal 18 30)</array>"; echo "Scout scheduled at 05:30 and 18:30."; fi
