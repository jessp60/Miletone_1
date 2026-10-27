#!/bin/sh
mkdir -p /app
python3 -c 'import sys;sys.path.insert(0,"/app");import server;server.init()'
pids=""
trap 'kill $pids 2>/dev/null; exit 0' INT TERM
for p in 8000 8001 8002; do
  PORT=$p python3 -c 'import os,sys;sys.path.insert(0,"/app");from server import *; port=int(os.environ["PORT"]); ThreadingHTTPServer(("127.0.0.1",port),H).serve_forever()' &
  pids="$pids $!"
done
wait
