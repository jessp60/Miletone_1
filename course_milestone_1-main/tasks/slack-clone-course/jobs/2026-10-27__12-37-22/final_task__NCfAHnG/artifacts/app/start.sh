#!/bin/sh
mkdir -p /app
python3 -c 'import sys;sys.path.insert(0,"/app");import server' 2>/dev/null || true
for p in 8000 8001 8002; do NODE_ID=$((p-8000)) PORT=$p python3 /app/server.py & echo $! > /app/node$((p-8000)).pid; done
wait
