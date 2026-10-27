#!/bin/sh
set -e
mkdir -p /app
python3 /app/server.py 8000 &
p0=$!
python3 /app/server.py 8001 &
p1=$!
python3 /app/server.py 8002 &
p2=$!
trap 'kill "$p0" "$p1" "$p2" 2>/dev/null || true' INT TERM EXIT
wait
