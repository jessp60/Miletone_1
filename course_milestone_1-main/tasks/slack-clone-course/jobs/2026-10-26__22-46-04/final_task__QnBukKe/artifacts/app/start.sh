#!/bin/sh
set -eu
mkdir -p /app
start_node() {
  p="$1"
  PORT="$p" nohup python3 /app/server.py >/tmp/huddle-$p.log 2>&1 &
  eval "PID_$p=$!"
  echo "$!" >/tmp/huddle-$p.pid
}
for p in 8000 8001 8002; do start_node "$p"; done
trap 'for p in 8000 8001 8002; do eval "x=\${PID_$p:-}"; [ -n "$x" ] && kill "$x" 2>/dev/null || true; done; exit 0' INT TERM EXIT
while :; do
  for p in 8000 8001 8002; do
    eval "x=\${PID_$p:-}"
    if ! kill -0 "$x" 2>/dev/null; then start_node "$p"; fi
  done
  sleep 1
done
