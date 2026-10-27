#!/bin/sh
set -eu
trap 'kill "$p0" "$p1" "$p2" 2>/dev/null || true; exit 0' TERM INT EXIT
python3 /app/server.py 8000 &
p0=$!
python3 /app/server.py 8001 &
p1=$!
python3 /app/server.py 8002 &
p2=$!
while :; do
  if ! kill -0 "$p0" 2>/dev/null; then
    python3 /app/server.py 8000 &
    p0=$!
  fi
  if ! kill -0 "$p1" 2>/dev/null; then
    python3 /app/server.py 8001 &
    p1=$!
  fi
  if ! kill -0 "$p2" 2>/dev/null; then
    python3 /app/server.py 8002 &
    p2=$!
  fi
  sleep 1
done
