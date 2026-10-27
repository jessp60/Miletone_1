# Read this first: work order and rules for this run

This run covers Component A (roles and invitations) and Component B (channels, messages,
mentions, read state, events), plus four baseline boot conditions. Before you run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`,
you MUST have run both checks below and seen them pass: **all 78** rules in `check_a.sh`, **all 93** rules in
`check_b.sh`, and **all four** conditions in `check_boot.sh`. Do not submit unless every one of them passes.

The task description you were given is still the grading contract. The files in `/spec` do
not change it; they tell you what to work on now and how to prove it works.

## Files in /spec

| File | What it is |
|---|---|
| `README.md` | This file: work order, how to verify, when you may submit |
| `spec_a.md` | The 78 rules for Component A |
| `spec_b.md` | The 93 rules (B1-B93) for Component B |
| `checks/check_a.sh` | Prints PASS or FAIL for each rule in `spec_a.md` |
| `checks/check_b.sh` | Prints PASS or FAIL for each rule in `spec_b.md` |
| `checks/check_boot.sh` | Prints PASS or FAIL for the four baseline boot conditions |

## The four baseline conditions (always required)

1. `GET /api/health` on `127.0.0.1:8000` returns `200` with `{"status": "ok", "node_id": 0}`.
2. Every node answers `GET /api/health` with `200` and its own `node_id`: port `8000` is `0`,
   port `8001` is `1`, port `8002` is `2`.
3. A WebSocket connection to `ws://127.0.0.1:8000/api/ws?token=<invalid>` is rejected: the
   connection attempt fails or closes. A bad token must never get an open connection.
4. If one HTTP node is killed (the grader sends `SIGKILL` to the node on port `8001`), the
   other two nodes keep answering `GET /api/health` with `200`. Each node must be its own
   process, so killing one does not take down the others.

Keep these true as you add features. `/app/start.sh` must stay in the foreground and keep the
cluster alive.

## Scope

Work on the routes in `spec_a.md` and `spec_b.md`. Build Component A first, then B on top of
it. Do NOT start on files, IRC, or the UI in this run. If `/app` is empty, first build the smallest
cluster that answers `GET /api/health` on ports `8000`, `8001`, and `8002`, then add the rest.
If `/app` already has code, improve it, do not start over.

## Storage: three processes share one state

The three HTTP nodes are separate processes, and a request on any node must see data written
through any other node (users, workspaces, channels, messages, event ids). Pick one shared store
that is safe for concurrent writers from three processes. For example, SQLite in WAL mode with a
busy timeout, short transactions, and no long-lived transaction held across a request. A store
that locks up under concurrent writes shows up as `got 000` failures in the checks. Every error
path (a duplicate username or slug, a validation failure) must roll back or commit before it
returns, and every response, errors included, must send a `Content-Length` (or close the
connection) so the client does not wait. A lock left open by one error makes the next request hang.
Also keep the per-channel event id sequence in that shared store so every node hands out the same
dense sequence. A WebSocket subscriber connected to one node must receive events caused by requests
handled by any node, for example by polling the shared event log.

## Order of work

1. Read `/spec/spec_a.md` and `/spec/spec_b.md`.
2. Run `check_a.sh` and `check_b.sh` before changing anything, to see what already passes.
   **Run them as two separate commands, never chained in one command** (see "How to verify").
3. Build in this order: auth (1-9), workspaces (10-21), basic channels (22-26), roles (27-36),
   channel permissions (37-41), invitations (42-55), join mode (56-62), ownership transfer
   (63-67), extra invitation and auth rules (68-72), API conventions (73-78), then Component B: channel management (B1-B22), messages (B23-B37), search and pins
   (B38-B45), mentions (B46-B53), events (B54-B63), read state (B64-B67), extra length and paging rules (B68-B72),
   API conventions for every route (B73-B78), edit, delete and reactions (B79-B89), and their events (B90-B93).
4. Fix one failing rule at a time, lowest number first.
5. After every change, run `check_a.sh` and then `check_b.sh` (two commands). A change for Component B
   can break Component A, so keep both green the whole time.
6. If a rule that passed starts failing, undo your last change before doing anything else.
7. When `check_a.sh` and `check_b.sh` are fully green, run `check_boot.sh` (conditions 1-3).
8. Run `check_boot.sh --kill` last (condition 4). It kills the node on port `8001` and may
   leave it down.
9. After step 8, make sure all three nodes answer `/api/health` again. If node `1` did not come
   back, stop the processes on `8000`, `8001`, and `8002` by PID (find them with `ss -ltnp`,
   or use the PID you saved when you started `start.sh`), start `/app/start.sh` again in the
   background, and confirm `200` on all three ports.

A line that starts with `FAIL SETUP` means a step the later rules depend on failed (for
example, registering a user or creating an invitation). Fix that first, because many rules
fail as a side effect.

## How to verify

Run each of these as its own, separate command. Do NOT chain `check_a.sh` and `check_b.sh` in one
command: each can take about 20 seconds, and your shell kills any command that runs longer than 30
seconds.

```bash
bash /spec/checks/check_a.sh
bash /spec/checks/check_b.sh
bash /spec/checks/check_boot.sh
bash /spec/checks/check_boot.sh --kill
```

Each prints one line per rule or condition and a final summary:

```text
PASS 1 register returns 201 with user and token
FAIL 31 admin changes another admin: expected 403, got 200
RESULT: 66 passed, 1 failed
```

- Each check starts `/app/start.sh` itself if the cluster is not answering. Your shell kills any
  command that runs longer than 30 seconds, so `check_a.sh` and `check_b.sh` stop sending requests after about
  20 seconds and still prints its summary. If you see `got 000` failures and a NOTE about the time
  limit, some route is slow or hangs (no answer within 3 seconds): fix the first `got 000` first.
- `check_a.sh` and `check_b.sh` use new usernames and slugs every run, so you can run them as often as you like.
- After you change code, restart the cluster before checking again: find the process that
  owns each port and stop it by PID. Never stop processes by name.

## Before you submit

You may submit only when all of these are true, from output you actually saw **after your last
code change** (if you change code after a check, run the checks again):

- the last line of `check_a.sh` is `RESULT: 78 passed, 0 failed`;
- the last line of `check_b.sh` is `RESULT: 93 passed, 0 failed`;
- `check_boot.sh --kill` ended with `RESULT: 4 passed, 0 failed`;
- after that, `GET /api/health` returns `200` on ports `8000`, `8001`, and `8002`.

These checks are necessary, not sufficient. The full task in the grading contract still
applies.