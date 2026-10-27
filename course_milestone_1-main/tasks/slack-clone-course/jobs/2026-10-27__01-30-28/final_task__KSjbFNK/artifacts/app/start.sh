#!/bin/sh
mkdir -p /app
for p in 8000 8001 8002; do PORT=$p python3 /app/app.py >/tmp/huddle-$p.log 2>&1 & echo $! > /tmp/huddle-$p.pid; done
wait
