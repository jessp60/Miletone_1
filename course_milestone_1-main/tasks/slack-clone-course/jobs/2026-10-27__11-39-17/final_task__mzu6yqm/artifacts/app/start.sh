#!/bin/sh
mkdir -p /app/data
python3 /app/server.py & P0=$!
NODE_ID=1 python3 /app/server.py & P1=$!
NODE_ID=2 python3 /app/server.py & P2=$!
trap 'kill $P0 $P1 $P2 2>/dev/null; wait' INT TERM EXIT
wait
