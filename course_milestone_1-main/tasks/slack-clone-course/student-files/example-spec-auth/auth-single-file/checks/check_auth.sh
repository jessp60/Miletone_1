#!/usr/bin/env bash
# Self-check for the auth example spec. Prints PASS/FAIL per rule in /spec/auth-single-file/auth.md.
# Usage: bash /spec/auth-single-file/checks/check_auth.sh      Exit 0 only if every rule passes.
set -u
N0=http://127.0.0.1:8000; N1=http://127.0.0.1:8001; N2=http://127.0.0.1:8002
BODY=/tmp/check_auth_body.json
pass=0; fail=0

ok()  { echo "PASS $1 $2"; pass=$((pass+1)); }
bad() { echo "FAIL $1 $2"; fail=$((fail+1)); }
# expect <rule> <description> <expected-status> <actual-status>
expect() { if [ "$4" = "$3" ]; then ok "$1" "$2"; else bad "$1" "$2: expected $3, got $4"; fi; }

# call <METHOD> <URL> [json-body] [token]  -> prints the HTTP status, saves the body in $BODY
call() {
  local args=(-s -o "$BODY" -w '%{http_code}' --max-time 5 -X "$1" "$2")
  [ -n "${3:-}" ] && args+=(-H 'Content-Type: application/json' -d "$3")
  [ -n "${4:-}" ] && args+=(-H "Authorization: Bearer $4")
  curl "${args[@]}" 2>/dev/null || true   # status 000 means no answer within 5s
}
# field <dotted.path>  -> prints that field of the last response body, or nothing
field() {
  python3 -c 'import json,sys
try:
    v=json.load(open(sys.argv[1]))
    for k in sys.argv[2].split("."): v=v[k]
    print(v)
except Exception: pass' "$BODY" "$1"
}
healthy() {
  for u in $N0 $N1 $N2; do curl -fs --max-time 2 "$u/api/health" >/dev/null 2>&1 || return 1; done
}

# AIDEV-NOTE: total wait is capped at 20s so the whole script fits the agent's 30s command limit.
if ! healthy; then
  (cd /app && nohup /app/start.sh >/tmp/start.log 2>&1 &)
  for _ in $(seq 1 20); do healthy && break; sleep 1; done
fi
healthy || { echo "FAIL 0 cluster is not answering /api/health on 8000, 8001, 8002 (see /tmp/start.log)"; echo "RESULT: 0 passed, 1 failed"; exit 1; }

USER="chk$(date +%s)$RANDOM"; PW="correct-horse-1"

s=$(call POST $N0/api/auth/register "{\"username\":\"$USER\",\"password\":\"$PW\"}")
TOKEN=$(field token); UNAME=$(field user.username)
if [ "$s" = 201 ] && [ "${#TOKEN}" -ge 16 ] && [ "$UNAME" = "$USER" ]; then
  ok 1 "register returns 201 with user and token"
else
  bad 1 "register: expected 201 with user.username and a token of 16+ chars, got status $s, token length ${#TOKEN}"
fi

s=$(call POST $N0/api/auth/register "{\"username\":\"$USER\",\"password\":\"$PW\"}")
expect 2 "duplicate username" 409 "$s"

s=$(call POST $N0/api/auth/register "{\"username\":\"bad name $RANDOM\",\"password\":\"$PW\"}")
expect 3 "username with a space" 400 "$s"

s=$(call POST $N0/api/auth/register "{\"username\":\"${USER}b\",\"password\":\"short77\"}")
expect 4 "7 character password" 400 "$s"

s=$(call POST $N0/api/auth/login "{\"username\":\"$USER\",\"password\":\"$PW\"}")
LTOKEN=$(field token)
if [ "$s" = 200 ] && [ -n "$LTOKEN" ]; then ok 5 "login returns 200 with user and token"
else bad 5 "login: expected 200 with a token, got status $s"; fi

s=$(call POST $N0/api/auth/login "{\"username\":\"$USER\",\"password\":\"WRONG12345\"}")
expect 6 "login with wrong password" 401 "$s"

s=$(call GET $N0/api/auth/me "" "$TOKEN")
if [ "$s" = 200 ] && [ "$(field user.username)" = "$USER" ]; then ok 7 "me returns 200 and the same username"
else bad 7 "me with token: expected 200 and username $USER, got status $s"; fi

s1=$(call GET $N0/api/auth/me); s2=$(call GET $N0/api/auth/me "" "not-a-real-token-000000")
if [ "$s1" = 401 ] && [ "$s2" = 401 ]; then ok 8 "me without a valid token returns 401"
else bad 8 "me: expected 401 with no token and with a made-up token, got $s1 and $s2"; fi

a=$(call POST $N2/api/auth/login "{\"username\":\"$USER\",\"password\":\"$PW\"}")
b=$(call GET $N1/api/auth/me "" "$TOKEN"); c=$(call GET $N2/api/auth/me "" "$TOKEN")
if [ "$a" = 200 ] && [ "$b" = 200 ] && [ "$c" = 200 ]; then ok 9 "account and token work on all three nodes"
else bad 9 "cross-node: login on 8002 got $a, me on 8001 got $b, me on 8002 got $c (expected 200 each)"; fi

echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
