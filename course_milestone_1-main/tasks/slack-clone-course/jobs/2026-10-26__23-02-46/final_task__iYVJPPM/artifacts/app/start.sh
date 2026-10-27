#!/bin/sh
set -u
[ -f /tmp/huddle_state.json ] || : 
run_node() {
  PORT="$1" NODE="$2" python3 /app/app.py >>"/tmp/huddle${2}.log" 2>&1 &
  echo $!
}
p0=$(run_node 8000 0); p1=$(run_node 8001 1); p2=$(run_node 8002 2)
trap 'kill "$p0" "$p1" "$p2" 2>/dev/null || true; exit 0' INT TERM EXIT
while :; do
  if ! kill -0 "$p0" 2>/dev/null; then p0=$(run_node 8000 0); fi
  if ! kill -0 "$p1" 2>/dev/null; then p1=$(run_node 8001 1); fi
  if ! kill -0 "$p2" 2>/dev/null; then p2=$(run_node 8002 2); fi
  sleep 1
done
