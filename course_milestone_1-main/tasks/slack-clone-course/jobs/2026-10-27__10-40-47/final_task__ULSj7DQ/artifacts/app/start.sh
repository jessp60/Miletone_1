#!/bin/sh
mkdir -p /app
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/app/data.sqlite3'); c.execute('PRAGMA journal_mode=WAL'); c.close()
PY
python3 /app/server.py & p0=$!
NODE_ID=1 python3 /app/server.py & p1=$!
NODE_ID=2 python3 /app/server.py & p2=$!
trap 'kill $p0 $p1 $p2 2>/dev/null' INT TERM EXIT
wait
