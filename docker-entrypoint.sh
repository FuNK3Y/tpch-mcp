#!/bin/sh
# Start a virtual display for the headed Chromium without touching stdout (stdout = MCP channel).
# (xvfb-run is not used: it merges stderr into stdout.)
set -e
if [ -z "$DISPLAY" ]; then
  Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/dev/null 2>&1 &
  export DISPLAY=:99
  i=0; while [ ! -e /tmp/.X11-unix/X99 ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
fi
if [ "$#" -eq 0 ]; then
  exec python -m tpch_mcp
fi
exec "$@"
