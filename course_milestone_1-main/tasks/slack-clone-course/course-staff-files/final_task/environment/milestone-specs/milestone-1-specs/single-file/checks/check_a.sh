#!/usr/bin/env bash
# Self-check for Component A (roles and invitations). Prints PASS/FAIL per rule in /spec/spec_a.md.
# Usage: bash /spec/checks/check_a.sh      Exit 0 only if every rule passes.
set -u
N0=http://127.0.0.1:8000; N1=http://127.0.0.1:8001; N2=http://127.0.0.1:8002
BODY=/tmp/check_a_body.json
pass=0; fail=0
# AIDEV-NOTE: the agent's shell kills any command after 30s and shows no output. After BUDGET
# seconds, call() stops sending requests so the script still finishes and prints its summary.
START=$SECONDS; BUDGET=20

ok()  { echo "PASS $1 $2"; pass=$((pass+1)); }
bad() { echo "FAIL $1 $2"; fail=$((fail+1)); }
# expect <rule> <description> <expected-status> <actual-status>
expect() { if [ "$4" = "$3" ]; then ok "$1" "$2"; else bad "$1" "$2: expected $3, got $4"; fi; }
# setup <description> <expected-status> <actual-status>: a setup step the rules depend on
setup() { [ "$3" = "$2" ] || bad SETUP "$1: expected $2, got $3"; }
# pass_if <id> <description> <detail-if-failed> <command...>: PASS when the command succeeds
pass_if() { local id=$1 d=$2 det=$3; shift 3; if "$@"; then ok "$id" "$d"; else bad "$id" "$d: $det"; fi; }
is2xx() { case "$1" in 2??) return 0;; *) return 1;; esac; }

# call <METHOD> <URL> [json-body] [token]  -> prints the HTTP status, saves the body in $BODY
call() {
  rm -f "$BODY"   # so a request that gets no answer never shows the previous response
  if [ $((SECONDS-START)) -ge $BUDGET ]; then echo 000; return; fi
  local args=(-s -o "$BODY" -w '%{http_code}' --max-time 3 -X "$1" "$2")
  [ -n "${3:-}" ] && args+=(-H 'Content-Type: application/json' -d "$3")
  [ -n "${4:-}" ] && args+=(-H "Authorization: Bearer $4")
  curl "${args[@]}" 2>/dev/null || true   # status 000 means no answer within 3s
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
# member_role <user_id>  -> role of that user in the last members response, or nothing
member_role() {
  python3 -c 'import json,sys
try:
    for m in json.load(open(sys.argv[1]))["members"]:
        if str(m["user_id"])==sys.argv[2]: print(m["role"])
except Exception: pass' "$BODY" "$1"
}
# jid <id> -> prints the id as a JSON value (bare number, or quoted if it is not numeric)
jid() { case "$1" in ''|*[!0-9]*) printf '"%s"' "$1";; *) printf '%s' "$1";; esac; }
healthy() {
  for u in $N0 $N1 $N2; do curl -fs --max-time 2 "$u/api/health" >/dev/null 2>&1 || return 1; done
}

# AIDEV-NOTE: total wait is capped at 15s so the whole script fits the agent's 30s command limit.
if ! healthy; then
  (cd /app && nohup /app/start.sh >/tmp/start.log 2>&1 &)
  for _ in $(seq 1 15); do healthy && break; sleep 1; done
fi
healthy || { echo "FAIL 0 cluster is not answering /api/health on 8000, 8001, 8002 (see /tmp/start.log)"; echo "RESULT: 0 passed, 1 failed"; exit 1; }

RUN="$RANDOM$RANDOM"; PW="correct-horse-1"

# mkuser <name>: registers a fresh user and sets <name>_TOKEN, <name>_ID
mkuser() {
  local u="u${RUN}$1" tv="$1_TOKEN"
  call POST $N0/api/auth/register "{\"username\":\"$u\",\"password\":\"$PW\"}" >/dev/null
  printf -v "$1_TOKEN" '%s' "$(field token)"
  printf -v "$1_ID" '%s' "$(field user.id)"
  [ -n "${!tv}" ] || bad SETUP "could not register user $1 (no token returned)"
}

########## AUTH (rules 1-9) ##########
AU="u${RUN}a"

s=$(call POST $N0/api/auth/register "{\"username\":\"$AU\",\"password\":\"$PW\"}")
TOKEN=$(field token); UNAME=$(field user.username)
if [ "$s" = 201 ] && [ "${#TOKEN}" -ge 16 ] && [ "$UNAME" = "$AU" ]; then
  ok 1 "register returns 201 with user and token"
else
  bad 1 "register: expected 201 with user.username and a token of 16+ chars, got status $s, token length ${#TOKEN}"
fi

s=$(call POST $N0/api/auth/register "{\"username\":\"$AU\",\"password\":\"$PW\"}")
expect 2 "duplicate username" 409 "$s"

s=$(call POST $N0/api/auth/register "{\"username\":\"bad name $RANDOM\",\"password\":\"$PW\"}")
expect 3 "username with a space" 400 "$s"

s=$(call POST $N0/api/auth/register "{\"username\":\"${AU}b\",\"password\":\"short77\"}")
expect 4 "7 character password" 400 "$s"

s=$(call POST $N0/api/auth/login "{\"username\":\"$AU\",\"password\":\"$PW\"}")
LTOKEN=$(field token)
if [ "$s" = 200 ] && [ -n "$LTOKEN" ]; then ok 5 "login returns 200 with user and token"
else bad 5 "login: expected 200 with a token, got status $s"; fi

s=$(call POST $N0/api/auth/login "{\"username\":\"$AU\",\"password\":\"WRONG12345\"}")
expect 6 "login with wrong password" 401 "$s"

s=$(call GET $N0/api/auth/me "" "$TOKEN")
if [ "$s" = 200 ] && [ "$(field user.username)" = "$AU" ]; then ok 7 "me returns 200 and the same username"
else bad 7 "me with token: expected 200 and username $AU, got status $s"; fi

s1=$(call GET $N0/api/auth/me); s2=$(call GET $N0/api/auth/me "" "not-a-real-token-000000")
if [ "$s1" = 401 ] && [ "$s2" = 401 ]; then ok 8 "me without a valid token returns 401"
else bad 8 "me: expected 401 with no token and with a made-up token, got $s1 and $s2"; fi

a=$(call POST $N2/api/auth/login "{\"username\":\"$AU\",\"password\":\"$PW\"}")
b=$(call GET $N1/api/auth/me "" "$TOKEN"); c=$(call GET $N2/api/auth/me "" "$TOKEN")
if [ "$a" = 200 ] && [ "$b" = 200 ] && [ "$c" = 200 ]; then ok 9 "account and token work on all three nodes"
else bad 9 "cross-node: login on 8002 got $a, me on 8001 got $b, me on 8002 got $c (expected 200 each)"; fi

# Users for the rest of the run (registered once, used by the rules below)
for n in owner admin1 admin2 member1 member2 guest1 out1 out2 inv1 inv2 tx ty; do mkuser $n; done

########## WORKSPACES (rules 10-21) ##########
SLUG="w$RUN"
s=$(call POST $N0/api/workspaces "{\"slug\":\"$SLUG\",\"name\":\"Check Workspace\"}" "$owner_TOKEN")
WID=$(field workspace.id); WSLUG=$(field workspace.slug); WNAME=$(field workspace.name)
WOWNER=$(field workspace.owner_id); WMODE=$(field workspace.join_mode)
GCH=$(field general_channel.id); GNAME=$(field general_channel.name)
if [ "$s" = 201 ] && [ -n "$WID" ] && [ "$WSLUG" = "$SLUG" ] && [ -n "$WNAME" ] && [ -n "$WOWNER" ] && [ -n "$WMODE" ] && [ -n "$GCH" ]; then
  ok 10 "create workspace returns 201 with workspace and general_channel"
else
  bad 10 "create workspace: expected 201 with workspace {id, slug, name, owner_id, join_mode} and general_channel, got status $s (id='$WID' slug='$WSLUG' owner_id='$WOWNER' join_mode='$WMODE' general_channel.id='$GCH')"
fi
if [ "$WMODE" = open ]; then ok 11 "join_mode defaults to open"; else bad 11 "join_mode: expected open, got '$WMODE'"; fi
if [ -n "$owner_ID" ] && [ "$WOWNER" = "$owner_ID" ]; then ok 12 "workspace.owner_id is the creator"; else bad 12 "owner_id: expected $owner_ID, got '$WOWNER'"; fi
if [ "$GNAME" = general ]; then ok 13 "general_channel is named general"; else bad 13 "general_channel.name: expected general, got '$GNAME'"; fi

s=$(call GET $N0/api/workspaces/$SLUG/members "" "$owner_TOKEN")
r=$(member_role "$owner_ID")
if [ "$s" = 200 ] && [ "$r" = owner ]; then ok 14 "members list shows the creator as owner"
else bad 14 "members: expected 200 and role owner for the creator, got status $s and role '$r'"; fi

s=$(call POST $N0/api/workspaces "{\"slug\":\"$SLUG\",\"name\":\"Again\"}" "$owner_TOKEN")
expect 15 "duplicate workspace slug" 409 "$s"

s=$(call POST $N0/api/workspaces '{"slug":"A","name":"Bad"}' "$owner_TOKEN")
expect 16 "invalid workspace slug" 400 "$s"

s=$(call POST $N0/api/workspaces "{\"slug\":\"x$RUN\",\"name\":\"NoAuth\"}")
expect 17 "create workspace without a token" 401 "$s"

s=$(call GET $N0/api/workspaces "" "$owner_TOKEN")
if [ "$s" = 200 ] && grep -q "\"$SLUG\"" "$BODY"; then ok 18 "owner lists their workspace"
else bad 18 "list workspaces as owner: expected 200 including $SLUG, got status $s"; fi

s=$(call GET $N0/api/workspaces "" "$out1_TOKEN")
if [ "$s" = 200 ] && ! grep -q "\"$SLUG\"" "$BODY"; then ok 19 "non-member does not see the workspace"
else bad 19 "list workspaces as non-member: expected 200 without $SLUG, got status $s"; fi

s1=$(call GET $N1/api/workspaces "" "$owner_TOKEN"); g1=0; grep -q "\"$SLUG\"" "$BODY" && g1=1
s2=$(call GET $N2/api/workspaces "" "$owner_TOKEN"); g2=0; grep -q "\"$SLUG\"" "$BODY" && g2=1
if [ "$s1" = 200 ] && [ "$s2" = 200 ] && [ $g1 = 1 ] && [ $g2 = 1 ]; then ok 20 "workspace is visible on all three nodes"
else bad 20 "cross-node: list on 8001 got $s1 (found=$g1), on 8002 got $s2 (found=$g2); expected 200 and found on both"; fi

s=$(call GET $N0/api/workspaces/nope$RUN/members "" "$owner_TOKEN")
expect 21 "members of an unknown workspace" 404 "$s"

# Add users to the workspace through invitations (setup for the rules below)
join_ws() {
  local tv="$1_TOKEN" s code
  s=$(call POST $N0/api/workspaces/$SLUG/invitations '{}' "$owner_TOKEN")
  code=$(field invitation.code)
  if [ "$s" != 201 ] || [ -z "$code" ]; then bad SETUP "invitation for $1: expected 201 with a code, got status $s"; return; fi
  s=$(call POST $N0/api/invitations/$code/accept '{}' "${!tv}")
  is2xx "$s" || bad SETUP "$1 accepting an invitation: expected 2xx, got $s"
}
for n in admin1 admin2 member1 member2 guest1; do join_ws $n; done

########## BASIC CHANNELS (rules 22-26) ##########
s=$(call POST $N0/api/workspaces/$SLUG/channels "{\"name\":\"pub-$RUN\"}" "$owner_TOKEN")
CH_PUB=$(field channel.id)
if [ "$s" = 201 ] && [ -n "$CH_PUB" ]; then ok 22 "owner creates a public channel"
else bad 22 "create public channel as owner: expected 201 with channel.id, got status $s"; fi

s=$(call POST $N0/api/workspaces/$SLUG/channels "{\"name\":\"priv-$RUN\",\"is_private\":true}" "$owner_TOKEN")
CH_PRIV=$(field channel.id); PRIV=$(field channel.is_private)
if [ "$s" = 201 ] && [ -n "$CH_PRIV" ] && [ "$PRIV" = True ]; then ok 23 "owner creates a private channel"
else bad 23 "create private channel as owner: expected 201 with is_private true, got status $s, is_private '$PRIV'"; fi

s=$(call POST $N0/api/channels/999999999/join '{}' "$member1_TOKEN")
expect 24 "join a channel that does not exist" 404 "$s"

s=$(call POST $N0/api/channels/$CH_PUB/join '{}' "$member1_TOKEN")
expect 25 "member joins a public channel" 200 "$s"

s=$(call POST $N0/api/channels/$CH_PRIV/join '{}' "$member1_TOKEN")
expect 26 "join a private channel without an invite" 403 "$s"

########## ROLES (rules 27-36) ##########
P="$N0/api/workspaces/$SLUG/members"

s=$(call PATCH $P/$admin1_ID '{"role":"admin"}' "$owner_TOKEN")
if [ "$s" = 200 ] && [ "$(field member.role)" = admin ] && [ "$(field member.user_id)" = "$admin1_ID" ]; then
  ok 27 "owner promotes a member to admin"
else bad 27 "owner promotes admin1: expected 200 and member {user_id: $admin1_ID, role: admin}, got status $s and role '$(field member.role)'"; fi

call GET $P "" "$owner_TOKEN" >/dev/null
r=$(member_role "$admin1_ID")
if [ "$r" = admin ]; then ok 28 "members list shows the new role"; else bad 28 "members list: expected admin1 to have role admin, got '$r'"; fi

s=$(call PATCH $P/$admin2_ID '{"role":"admin"}' "$owner_TOKEN"); setup "promote admin2" 200 "$s"
s=$(call PATCH $P/$member2_ID '{"role":"admin"}' "$owner_TOKEN"); setup "promote member2" 200 "$s"

s=$(call PATCH $P/$member2_ID '{"role":"member"}' "$owner_TOKEN")
if [ "$s" = 200 ] && [ "$(field member.role)" = member ]; then ok 29 "owner demotes an admin to member"
else bad 29 "owner demotes member2: expected 200 and role member, got status $s and role '$(field member.role)'"; fi

s=$(call PATCH $P/$guest1_ID '{"role":"guest"}' "$admin1_TOKEN")
if [ "$s" = 200 ] && [ "$(field member.role)" = guest ]; then ok 30 "admin sets a member to guest"
else bad 30 "admin1 sets guest1 to guest: expected 200 and role guest, got status $s and role '$(field member.role)'"; fi

s=$(call PATCH $P/$admin2_ID '{"role":"member"}' "$admin1_TOKEN")
expect 31 "admin changes another admin" 403 "$s"

s=$(call PATCH $P/$owner_ID '{"role":"member"}' "$admin1_TOKEN")
expect 32 "admin changes the owner" 403 "$s"

s=$(call PATCH $P/$member1_ID '{"role":"owner"}' "$owner_TOKEN")
expect 33 "set role to owner through PATCH" 400 "$s"

s=$(call PATCH $P/$owner_ID '{"role":"admin"}' "$owner_TOKEN")
expect 34 "owner changes their own role" 403 "$s"

s=$(call PATCH $P/$member2_ID '{"role":"admin"}' "$member1_TOKEN")
expect 35 "plain member changes a role" 403 "$s"

s=$(call PATCH $P/$member1_ID '{"role":"superuser"}' "$owner_TOKEN")
expect 36 "unknown role value" 400 "$s"

########## ROLE-BASED CHANNEL PERMISSIONS (rules 37-41) ##########
s=$(call POST $N0/api/channels/$CH_PUB/join '{}' "$guest1_TOKEN")
expect 37 "guest joins a public channel" 403 "$s"

s=$(call POST $N0/api/workspaces/$SLUG/channels "{\"name\":\"g-$RUN\"}" "$guest1_TOKEN")
expect 38 "guest creates a channel" 403 "$s"

s=$(call POST $N0/api/workspaces/$SLUG/channels "{\"name\":\"m-pub-$RUN\"}" "$member1_TOKEN")
expect 39 "member creates a public channel" 201 "$s"

s=$(call POST $N0/api/workspaces/$SLUG/channels "{\"name\":\"m-priv-$RUN\",\"is_private\":true}" "$member1_TOKEN")
expect 40 "member creates a private channel" 403 "$s"

s=$(call POST $N0/api/workspaces/$SLUG/channels "{\"name\":\"a-priv-$RUN\",\"is_private\":true}" "$admin1_TOKEN")
expect 41 "admin creates a private channel" 201 "$s"

########## INVITATIONS (rules 42-55) ##########
I="$N0/api/workspaces/$SLUG/invitations"

s=$(call POST $I '{}' "$admin1_TOKEN")
CODE1=$(field invitation.code)
if [ "$s" = 201 ] && [ -n "$CODE1" ]; then ok 42 "admin creates an invitation with a code"
else bad 42 "admin creates invitation: expected 201 with invitation.code, got status $s"; fi

s=$(call POST $I '{}' "$member1_TOKEN")
expect 43 "member creates an invitation" 403 "$s"

s=$(call GET $I "" "$owner_TOKEN")
if [ "$s" = 200 ] && [ -n "$CODE1" ] && grep -q "$CODE1" "$BODY"; then ok 44 "owner lists active invitations"
else bad 44 "list invitations as owner: expected 200 including code '$CODE1', got status $s"; fi

s=$(call GET $I "" "$member1_TOKEN")
expect 45 "member lists invitations" 403 "$s"

s=$(call POST $N0/api/invitations/$CODE1/accept '{}' "$inv1_TOKEN")
call GET $P "" "$owner_TOKEN" >/dev/null
r=$(member_role "$inv1_ID")
if is2xx "$s" && [ -n "$r" ]; then ok 46 "accepting an invitation adds the user to the workspace"
else bad 46 "accept invitation: expected 2xx and inv1 in members list, got status $s and role '$r'"; fi

s=$(call POST $N0/api/invitations/$CODE1/accept '{}' "$inv2_TOKEN")
expect 47 "accept an exhausted invitation (default max_uses is 1)" 404 "$s"

s=$(call POST $N0/api/invitations/nosuchcode$RUN/accept '{}' "$inv2_TOKEN")
expect 48 "accept an unknown invitation code" 404 "$s"

s=$(call POST $I '{"expires_in_seconds":1}' "$owner_TOKEN"); CODE_EXP=$(field invitation.code)
setup "create an expiring invitation" 201 "$s"
sleep 2
s=$(call POST $N0/api/invitations/$CODE_EXP/accept '{}' "$inv2_TOKEN")
expect 49 "accept an expired invitation" 404 "$s"

s=$(call POST $I '{}' "$admin1_TOKEN"); CODE_REV=$(field invitation.code)
setup "create an invitation to revoke" 201 "$s"
s=$(call DELETE $I/$CODE_REV "" "$admin1_TOKEN")
if [ "$s" = 200 ] && [ "$(field deleted)" = True ]; then ok 50 "admin revokes an invitation"
else bad 50 "revoke invitation: expected 200 with {\"deleted\":true}, got status $s and deleted '$(field deleted)'"; fi

s=$(call POST $N0/api/invitations/$CODE_REV/accept '{}' "$inv2_TOKEN")
expect 51 "accept a revoked invitation" 404 "$s"

s=$(call POST $I '{}' "$owner_TOKEN"); CODE_KEEP=$(field invitation.code)
setup "create an invitation for the delete check" 201 "$s"
s=$(call DELETE $I/$CODE_KEEP "" "$member1_TOKEN")
expect 52 "member revokes an invitation" 403 "$s"

s=$(call POST $I "{\"invited_user_id\":$(jid "$tx_ID")}" "$owner_TOKEN"); CODE_T=$(field invitation.code)
setup "create a targeted invitation" 201 "$s"
s=$(call POST $N0/api/invitations/$CODE_T/accept '{}' "$ty_TOKEN")
expect 53 "wrong user accepts a targeted invitation" 403 "$s"

s=$(call POST $N0/api/invitations/$CODE_T/accept '{}' "$tx_TOKEN")
if is2xx "$s"; then ok 54 "the targeted user accepts their invitation"
else bad 54 "targeted user accepts: expected 2xx, got $s"; fi

s=$(call POST $N0/api/invitations/$CODE_KEEP/accept '{}')
expect 55 "accept an invitation without a token" 401 "$s"

########## MORE INVITATION AND AUTH RULES (68-72) ##########
for n in x1 x2 x3 t1; do mkuser $n; done
s=$(call POST $I '{"max_uses":2}' "$owner_TOKEN"); CODE_M=$(field invitation.code)
setup "create an invitation with max_uses 2" 201 "$s"
sa=$(call POST $N0/api/invitations/$CODE_M/accept '{}' "$x1_TOKEN")
sb=$(call POST $N0/api/invitations/$CODE_M/accept '{}' "$x2_TOKEN")
sc=$(call POST $N0/api/invitations/$CODE_M/accept '{}' "$x3_TOKEN")
if is2xx "$sa" && is2xx "$sb" && [ "$sc" = 404 ]; then ok 68 "max_uses 2 allows two accepts and a third gets 404"
else bad 68 "max_uses 2: expected accept 2xx, 2xx, then 404, got $sa, $sb, $sc"; fi

call GET $I "" "$owner_TOKEN" >/dev/null
if grep -q "$CODE_M" "$BODY"; then bad 69 "exhausted invitation: expected it to be missing from the list, but code $CODE_M is still listed"
else ok 69 "an exhausted invitation is not listed"; fi
if grep -q "$CODE_REV" "$BODY"; then bad 70 "revoked invitation: expected it to be missing from the list, but code $CODE_REV is still listed"
else ok 70 "a revoked invitation is not listed"; fi

s=$(call POST $I "{\"invited_user_id\":$(jid "$t1_ID"),\"max_uses\":5}" "$owner_TOKEN"); CODE_T2=$(field invitation.code)
setup "create a targeted invitation with max_uses 5" 201 "$s"
sa=$(call POST $N0/api/invitations/$CODE_T2/accept '{}' "$t1_TOKEN")
sb=$(call POST $N0/api/invitations/$CODE_T2/accept '{}' "$t1_TOKEN")
if is2xx "$sa" && [ "$sb" = 404 ]; then ok 71 "a targeted invitation is single-use even with max_uses 5"
else bad 71 "targeted invitation single-use: expected first accept 2xx then 404, got $sa then $sb"; fi

s=$(call POST $N0/api/auth/register "{\"username\":\"u${RUN}-x!\",\"password\":\"$PW\"}")
expect 72 "register with special characters in the username" 400 "$s"

########## API CONVENTIONS (rules 73-78) ##########
# AIDEV-NOTE: each route is "METHOD|PATH|BODY". sweep <expected-status> <token> <routes...> prints the routes that did not answer the expected status.
sweep() {
  local exp=$1 tok=$2 line m p b s out=""; shift 2
  for line in "$@"; do
    IFS='|' read -r m p b <<<"$line"
    s=$(call "$m" "$N0$p" "$b" "$tok")
    [ "$s" = "$exp" ] || out="$out $m $p -> $s;"
  done
  echo "$out"
}
A_ROUTES=(
  "GET|/api/workspaces|"
  "GET|/api/workspaces/$SLUG/members|"
  "PATCH|/api/workspaces/$SLUG/members/$member1_ID|{\"role\":\"member\"}"
  "PATCH|/api/workspaces/$SLUG|{\"name\":\"Conv\"}"
  "POST|/api/workspaces/$SLUG/invitations|{}"
  "GET|/api/workspaces/$SLUG/invitations|"
  "DELETE|/api/workspaces/$SLUG/invitations/nosuchcode|"
  "POST|/api/workspaces/$SLUG/transfer_ownership|{\"new_owner_id\":1}"
  "POST|/api/workspaces/$SLUG/channels|{\"name\":\"zz-$RUN\"}"
  "POST|/api/channels/$CH_PUB/join|{}"
)
r=$(sweep 401 "" "${A_ROUTES[@]}")
pass_if 73 "every authenticated route returns 401 with no token" "routes that did not return 401:$r" [ -z "$r" ]
r=$(sweep 401 "not-a-real-token-000000" "${A_ROUTES[@]}")
pass_if 74 "every authenticated route returns 401 with an invalid token" "routes that did not return 401:$r" [ -z "$r" ]
r=$(sweep 404 "$owner_TOKEN" \
  "PATCH|/api/workspaces/nope$RUN/members/$member1_ID|{\"role\":\"member\"}" \
  "PATCH|/api/workspaces/$SLUG/members/999999999|{\"role\":\"member\"}" \
  "PATCH|/api/workspaces/nope$RUN|{\"name\":\"Conv\"}" \
  "GET|/api/workspaces/nope$RUN/invitations|" \
  "POST|/api/workspaces/nope$RUN/invitations|{}" \
  "POST|/api/workspaces/nope$RUN/transfer_ownership|{\"new_owner_id\":1}" \
  "POST|/api/workspaces/nope$RUN/channels|{\"name\":\"zz-$RUN\"}")
pass_if 75 "unknown workspace or user ids return 404" "routes that did not return 404:$r" [ -z "$r" ]
r=$(sweep 400 "$owner_TOKEN" \
  "POST|/api/workspaces|{\"slug\":\"cv-$RUN\"}" \
  "POST|/api/workspaces/$SLUG/channels|{}" \
  "PATCH|/api/workspaces/$SLUG/members/$member1_ID|{}")
r2=$(sweep 400 "" "POST|/api/auth/register|{\"username\":\"cv$RUN\"}")
pass_if 76 "missing required fields return 400" "routes that did not return 400:$r$r2" [ -z "$r$r2" ]
r=$(sweep 400 "$owner_TOKEN" "POST|/api/workspaces|{not json" "POST|/api/workspaces/$SLUG/channels|{not json")
r2=$(sweep 400 "" "POST|/api/auth/register|{not json" "POST|/api/auth/login|{not json")
pass_if 77 "a malformed JSON body returns 400" "routes that did not return 400:$r$r2" [ -z "$r$r2" ]
mkuser cn
s=$(call POST $N1/api/workspaces/$SLUG/invitations '{}' "$owner_TOKEN"); CODE_CN=$(field invitation.code)
setup "create an invitation on node 1" 201 "$s"
sa=$(call POST $N2/api/invitations/$CODE_CN/accept '{}' "$cn_TOKEN")
call GET $N0/api/workspaces/$SLUG/members "" "$owner_TOKEN" >/dev/null
rcn=$(member_role "$cn_ID")
pass_if 78 "an invitation created on node 1 is accepted on node 2 and shows on node 0" "accept on node 2 got $sa, role seen on node 0: '$rcn' (expected a member role)" [ "$(echo $sa | cut -c1)" = 2 -a -n "$rcn" ]

########## JOIN MODE (rules 56-62) ##########
s=$(call POST $N0/api/channels/$CH_PUB/join '{}' "$out1_TOKEN")
call GET $P "" "$owner_TOKEN" >/dev/null
r=$(member_role "$out1_ID")
if [ "$s" = 200 ] && [ "$r" = member ]; then ok 56 "open workspace: a non-member joining a public channel becomes a member"
else bad 56 "open workspace join: expected 200 and out1 listed as member, got status $s and role '$r'"; fi

s=$(call PATCH $N0/api/workspaces/$SLUG '{"join_mode":"invite_only"}' "$owner_TOKEN")
if [ "$s" = 200 ] && [ "$(field workspace.join_mode)" = invite_only ]; then ok 57 "owner sets join_mode to invite_only"
else bad 57 "owner sets join_mode: expected 200 and workspace.join_mode invite_only, got status $s and '$(field workspace.join_mode)'"; fi

s=$(call PATCH $N0/api/workspaces/$SLUG '{"name":"Renamed Workspace"}' "$admin1_TOKEN")
if [ "$s" = 200 ] && [ "$(field workspace.name)" = "Renamed Workspace" ]; then ok 58 "admin renames the workspace"
else bad 58 "admin renames: expected 200 and workspace.name 'Renamed Workspace', got status $s and '$(field workspace.name)'"; fi

s=$(call PATCH $N0/api/workspaces/$SLUG '{"name":"Nope"}' "$member1_TOKEN")
expect 59 "member renames the workspace" 403 "$s"

s=$(call PATCH $N0/api/workspaces/$SLUG '{"join_mode":"closed"}' "$owner_TOKEN")
expect 60 "unknown join_mode value" 400 "$s"

s=$(call POST $N0/api/channels/$CH_PUB/join '{}' "$out2_TOKEN")
expect 61 "invite_only workspace: a non-member joins a public channel" 403 "$s"

s=$(call POST $I '{}' "$owner_TOKEN"); CODE_J=$(field invitation.code)
setup "create an invitation in an invite_only workspace" 201 "$s"
sa=$(call POST $N0/api/invitations/$CODE_J/accept '{}' "$out2_TOKEN")
s=$(call POST $N0/api/channels/$CH_PUB/join '{}' "$out2_TOKEN")
if is2xx "$sa" && [ "$s" = 200 ]; then ok 62 "invite_only workspace: redeeming an invitation allows joining"
else bad 62 "invite_only join after redeeming: expected accept 2xx then join 200, got accept $sa and join $s"; fi

########## OWNERSHIP TRANSFER (rules 63-67; these change the owner, so they run last) ##########
T="$N0/api/workspaces/$SLUG/transfer_ownership"

s=$(call POST $T "{\"new_owner_id\":$(jid "$member1_ID")}" "$owner_TOKEN")
expect 63 "transfer ownership to a plain member" 400 "$s"

s=$(call POST $T "{\"new_owner_id\":$(jid "$admin2_ID")}" "$admin1_TOKEN")
expect 64 "non-owner transfers ownership" 403 "$s"

s=$(call POST $T "{\"new_owner_id\":$(jid "$admin2_ID")}" "$owner_TOKEN")
if is2xx "$s"; then ok 65 "owner transfers ownership to an admin"
else bad 65 "owner transfers to admin2: expected 2xx, got $s"; fi

call GET $P "" "$admin2_TOKEN" >/dev/null
r_new=$(member_role "$admin2_ID"); r_old=$(member_role "$owner_ID")
if [ "$r_new" = owner ] && [ "$r_old" = admin ]; then ok 66 "after the transfer: target is owner, previous owner is admin"
else bad 66 "roles after transfer: expected admin2=owner and previous owner=admin, got '$r_new' and '$r_old'"; fi

s=$(call POST $T "{\"new_owner_id\":$(jid "$admin1_ID")}" "$owner_TOKEN")
expect 67 "previous owner transfers ownership again" 403 "$s"

if [ $((SECONDS-START)) -ge $BUDGET ]; then
  echo "NOTE: the check hit its ${BUDGET}s time limit and stopped sending requests, so every rule marked 'got 000' after that point failed only because of the limit. Some request is slow or hangs (more than 3s with no answer): find the first FAIL with 'got 000' and fix that route first."
fi
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]