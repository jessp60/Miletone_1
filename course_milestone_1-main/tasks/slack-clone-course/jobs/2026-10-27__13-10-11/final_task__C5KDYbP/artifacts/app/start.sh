#!/bin/sh
set -eu
mkdir -p /app
for p in 8000 8001 8002; do
  PORT=$p python3 /app/app.py &
  echo $! > /app/node_$p.pid
done
trap 'kill $(cat /app/node_8000.pid) $(cat /app/node_8001.pid) $(cat /app/node_8002.pid) 2>/dev/null || true' INT TERM EXIT
while :; do sleep 2; done
