#!/bin/sh
set -eu
mkdir -p /app/data
PORT=8000 nohup python3 /app/server.py >/tmp/huddle-8000.log 2>&1 &
echo $! > /tmp/huddle-8000.pid
sleep 1
PORT=8001 nohup python3 /app/server.py >/tmp/huddle-8001.log 2>&1 &
echo $! > /tmp/huddle-8001.pid
sleep 1
PORT=8002 nohup python3 /app/server.py >/tmp/huddle-8002.log 2>&1 &
echo $! > /tmp/huddle-8002.pid
wait
