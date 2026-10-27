#!/bin/sh
set -eu
mkdir -p /app
pids=""
trap 'kill $pids 2>/dev/null || true; wait 2>/dev/null || true' TERM INT EXIT
for n in 0 1 2; do
  PORT=$((8000+n)) NODE_ID=$n python3 /app/server.py &
  pids="$pids $!"
done
wait
