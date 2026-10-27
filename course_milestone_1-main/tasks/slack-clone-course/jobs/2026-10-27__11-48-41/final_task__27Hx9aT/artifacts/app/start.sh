#!/bin/sh
set -eu
mkdir -p /app/data
for i in 0 1 2; do PORT=$((8000+i)) NODE_ID=$i nohup python3 /app/server.py >/tmp/huddle-$i.log 2>&1 & echo $! > /tmp/huddle-$i.pid; done
wait
