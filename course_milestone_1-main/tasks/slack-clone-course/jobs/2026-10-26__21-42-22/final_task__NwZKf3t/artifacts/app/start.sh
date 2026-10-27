#!/bin/sh
set -eu
mkdir -p /app
[ -f /app/state.json ] || printf '%s' '{"users":{},"tokens":{},"workspaces":{},"channels":{},"invitations":{},"next":1}' > /app/state.json
pids=""
trap 'kill $pids 2>/dev/null || true; exit 0' TERM INT EXIT
while :; do
  for i in 0 1 2; do
    port=$((8000+i))
    if ! ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then
      NODE_ID=$i python3 /app/server.py "$port" >/tmp/node$i.log 2>&1 &
      pids="$pids $!"
    fi
  done
  sleep 1
done
