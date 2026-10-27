#!/bin/sh
set -eu
mkdir -p /app
python3 -c 'import sys;sys.path.insert(0,"/app");import server;server.init()'
pids=""
for p in 8000 8001 8002; do python3 /app/server.py "$p" & pids="$pids $!"; done
trap 'kill $pids 2>/dev/null || true' TERM INT EXIT
wait
