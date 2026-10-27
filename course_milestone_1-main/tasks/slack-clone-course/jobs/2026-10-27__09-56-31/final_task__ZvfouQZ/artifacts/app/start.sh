#!/bin/bash
set -u
mkdir -p /app
pids=()
cleanup(){ for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
for port in 8000 8001 8002; do
  (while true; do python3 /app/server.py "$port"; sleep 0.2; done) &
  pids+=("$!")
done
while true; do sleep 1; done
