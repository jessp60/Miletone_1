#!/bin/sh
set -e
mkdir -p /app
python3 - <<'PY2'
import sqlite3
c=sqlite3.connect('/app/huddle.db',timeout=30)
c.execute('PRAGMA journal_mode=WAL')
c.close()
PY2
for p in 8000 8001 8002; do PORT=$p python3 /app/server.py & echo $! > /app/node-$p.pid; done
wait
