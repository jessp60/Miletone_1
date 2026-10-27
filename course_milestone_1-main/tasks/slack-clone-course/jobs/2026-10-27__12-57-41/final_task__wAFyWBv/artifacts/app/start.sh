#!/bin/sh
set -e
mkdir -p /app/data
python3 -c 'import sys; sys.path.insert(0,"/app"); import server; server.db().close()'
NODE=0 python3 /app/server.py 8000 &
P0=$!
NODE=1 python3 /app/server.py 8001 &
P1=$!
NODE=2 python3 /app/server.py 8002 &
P2=$!
trap 'kill "$P0" "$P1" "$P2" 2>/dev/null || true' EXIT
wait
