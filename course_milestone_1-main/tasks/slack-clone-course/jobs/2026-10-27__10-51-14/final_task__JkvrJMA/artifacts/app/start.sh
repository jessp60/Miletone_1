#!/bin/sh
mkdir -p /app/data
python3 /app/server.py 0 >/tmp/huddle0.log 2>&1 & echo $! >/tmp/huddle0.pid
python3 /app/server.py 1 >/tmp/huddle1.log 2>&1 & echo $! >/tmp/huddle1.pid
python3 /app/server.py 2 >/tmp/huddle2.log 2>&1 & echo $! >/tmp/huddle2.pid
wait
