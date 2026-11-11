#!/bin/bash
# Daily automatic refresh (Phase 9) + hosted-dashboard publish, via macOS
# launchd. The job runs scripts/daily.sh.
#
#   scripts/schedule.sh on [HH:MM]   install/enable, default 09:00
#   scripts/schedule.sh off          pause (removes the job)
#   scripts/schedule.sh status       is it on, when, last log lines
#   scripts/schedule.sh run          trigger one run right now
#
# If the Mac is asleep at the scheduled time, launchd runs the job on wake.
# Output goes to data/logs/poll.log.

set -euo pipefail

LABEL="com.vishwa.incidentpipeline"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT/.venv/bin/python"
LOG_DIR="$PROJECT/data/logs"
LOG="$LOG_DIR/poll.log"

cmd="${1:-status}"

case "$cmd" in
  on)
    time="${2:-09:00}"
    hour=$((10#${time%%:*}))
    minute=$((10#${time##*:}))
    [ -x "$PYTHON" ] || { echo "No venv python at $PYTHON"; exit 1; }
    mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string><string>$PROJECT/scripts/daily.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <!-- Empty = anonymous GitLab access (the project is public). Remove
         this entry once .env has a valid token to use it instead. -->
    <key>GITLAB_TOKEN</key><string></string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$hour</integer>
    <key>Minute</key><integer>$minute</integer>
  </dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    printf "Scheduled: daily at %02d:%02d. Log: %s\n" "$hour" "$minute" "$LOG"
    ;;
  off)
    if [ -f "$PLIST" ]; then
      launchctl unload "$PLIST" 2>/dev/null || true
      rm "$PLIST"
      echo "Daily refresh turned off."
    else
      echo "Already off."
    fi
    ;;
  status)
    if launchctl list | grep -q "$LABEL"; then
      h=$(/usr/libexec/PlistBuddy -c "Print :StartCalendarInterval:Hour" "$PLIST")
      m=$(/usr/libexec/PlistBuddy -c "Print :StartCalendarInterval:Minute" "$PLIST")
      printf "ON - daily at %02d:%02d\n" "$h" "$m"
    else
      echo "OFF"
    fi
    if [ -f "$LOG" ]; then echo "--- last log lines ---"; tail -5 "$LOG"; fi
    ;;
  run)
    launchctl start "$LABEL" && echo "Started one run now. Log: $LOG"
    ;;
  *)
    echo "Usage: $0 {on [HH:MM]|off|status|run}"; exit 1
    ;;
esac
