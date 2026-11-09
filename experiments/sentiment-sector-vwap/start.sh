#!/usr/bin/env bash
# Start / stop / check the live paper run (sentiment-sector-vwap).
#
#   ./start.sh            run in this terminal (Ctrl-C stops it)
#   ./start.sh --bg       run in the background; console output -> logs/console.log
#   ./start.sh status     is it running? plus the last few console lines
#   ./start.sh tail       follow the console output of a background run
#   ./start.sh stop       stop a background run
#   ./start.sh check      one read of both pages + bars, then exit
#   ./start.sh login      sign in to stockmojo once, by hand (live data after 10:00 needs it)
#
# The process sleeps by itself outside market hours, so it can stay running.
# `caffeinate -i` stops the Mac idle-sleeping while it runs; closing the lid
# still sleeps a laptop unless it is on power with an external display.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../../backend" && pwd)"
PY="$BACKEND/.venv/bin/python"
SCRIPT="$HERE/scripts/run_live.py"
LOGS="$HERE/logs"
PIDFILE="$LOGS/run.pid"
mkdir -p "$LOGS"

running_pid() {
  [[ -f "$PIDFILE" ]] || return 1
  local pid; pid="$(cat "$PIDFILE")"
  kill -0 "$pid" 2>/dev/null && echo "$pid"
}

run() {
  cd "$BACKEND"   # the app's config, DB and stored Dhan token resolve from here
  CONFIG_PATH=conf PYTHONUNBUFFERED=1 exec caffeinate -i "$PY" "$SCRIPT" "$@"
}

case "${1:-}" in
  --bg)
    if pid="$(running_pid)"; then echo "Already running (pid $pid). ./start.sh status"; exit 1; fi
    nohup "$0" >>"$LOGS/console.log" 2>&1 &
    echo $! >"$PIDFILE"
    sleep 3
    if pid="$(running_pid)"; then
      echo "Started in the background (pid $pid)."
      echo "  watch it:  $0 tail      stop it:  $0 stop"
      tail -n 3 "$LOGS/console.log"
    else
      echo "It exited straight away. Last lines of logs/console.log:"; tail -n 20 "$LOGS/console.log"; exit 1
    fi
    ;;
  stop)
    if pid="$(running_pid)"; then
      pkill -TERM -P "$pid" 2>/dev/null || true; kill -TERM "$pid" 2>/dev/null || true
      rm -f "$PIDFILE"; echo "Stopped (pid $pid)."
    else
      echo "Not running in the background."
    fi
    ;;
  status)
    if pid="$(running_pid)"; then echo "RUNNING (pid $pid)"; else echo "NOT RUNNING (in the background)"; fi
    [[ -f "$LOGS/console.log" ]] && { echo "--- last console lines:"; tail -n 5 "$LOGS/console.log"; }
    ;;
  tail)
    exec tail -n 30 -f "$LOGS/console.log"
    ;;
  check)
    run --check
    ;;
  login)
    if pid="$(running_pid)"; then echo "Stop the running copy first (./start.sh stop): it holds the browser profile."; exit 1; fi
    cd "$BACKEND" && exec "$PY" "$HERE/scripts/stockmojo_login.py"
    ;;
  "")
    run
    ;;
  *)
    sed -n 2,13p "$0"; exit 2
    ;;
esac
