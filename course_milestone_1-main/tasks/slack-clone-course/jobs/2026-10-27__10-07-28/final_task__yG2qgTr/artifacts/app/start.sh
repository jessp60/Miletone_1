#!/bin/sh
set -e
mkdir -p /app
for p in 8000 8001 8002; do
  PORT=$p python3 /app/server.py &
done
wait
