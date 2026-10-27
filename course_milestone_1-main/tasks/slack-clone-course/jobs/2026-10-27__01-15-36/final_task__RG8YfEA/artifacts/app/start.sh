#!/bin/sh
mkdir -p /app/data
while :; do
 for n in 0 1 2; do
  p=$((8000+n))
  if ! (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then NODE_ID=$n python3 /app/server.py >/tmp/huddle-$n.log 2>&1 &
  fi
 done
 sleep 1
done
