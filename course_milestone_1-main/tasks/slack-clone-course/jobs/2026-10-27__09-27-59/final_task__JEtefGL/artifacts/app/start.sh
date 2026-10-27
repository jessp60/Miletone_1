#!/bin/sh
mkdir -p /app
for i in 0 1 2; do NODE_ID=$i python3 /app/server.py >/tmp/huddle-$i.log 2>&1 & echo $! > /tmp/huddle-$i.pid; done
trap 'kill $(cat /tmp/huddle-0.pid) $(cat /tmp/huddle-1.pid) $(cat /tmp/huddle-2.pid) 2>/dev/null; exit 0' TERM INT
while :; do sleep 1; done
