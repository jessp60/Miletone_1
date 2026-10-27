# Read this first: work order and rules for this run

This run has seven chained stages: three for Component A (roles and invitations) and four for
Component B (channels, message history, edit, delete and reactions), plus four baseline boot conditions. Work through the
stages **in order: A.1, A.2, A.3, then B.1, B.2, B.3, B.4**. Finish one stage
completely before you start the next. Before you run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`,
you MUST have seen all of these pass:

| Stage | Spec | Check | Rules | Passing line |
|---|---|---|---|---|
| A.1 | `spec_a1.md` | `checks/check_a1.sh` | 28 | `RESULT: 28 passed, 0 failed` |
| A.2 | `spec_a2.md` | `checks/check_a2.sh` | 20 | `RESULT: 20 passed, 0 failed` |
| A.3 | `spec_a3.md` | `checks/check_a3.sh` | 30 | `RESULT: 30 passed, 0 failed` |
| B.1 | `spec_b1.md` | `checks/check_b1.sh` | 22 | `RESULT: 22 passed, 0 failed` |
| B.2 | `spec_b2.md` | `checks/check_b2.sh` | 29 | `RESULT: 29 passed, 0 failed` |
| B.3 | `spec_b3.md` | `checks/check_b3.sh` | 18 | `RESULT: 18 passed, 0 failed` |
| B.4 | `spec_b4.md` | `checks/check_b4.sh` | 24 | `RESULT: 24 passed, 0 failed` |
| Boot | (below) | `checks/check_boot.sh` | 4 | `RESULT: 4 passed, 0 failed` |

The task description you were given is still the grading contract. The files in `/spec` do not
change it; they tell you what to work on now and how to prove it works.

## The four baseline conditions (always required)

1. `GET /api/health` on `127.0.0.1:8000` returns `200` with `{"status": "ok", "node_id": 0}`.
2. Every node answers `GET /api/health` with `200` and its own `node_id`: port `8000` is `0`,
   port `8001` is `1`, port `8002` is `2`.
3. A WebSocket connection to `ws://127.0.0.1:8000/api/ws?token=<invalid>` is rejected: the
   connection attempt fails or closes. A bad token must never get an open connection.
4. If one HTTP node is killed (the grader sends `SIGKILL` to the node on port `8001`), the
   other two nodes keep answering `GET /api/health` with `200`. Each node must be its own
   process, so killing one does not take down the others.

Keep these true in every stage. `/app/start.sh` must stay in the foreground and keep the
cluster alive.

## Scope

Work only on the routes in the seven stage specs. Do NOT start on files, IRC, or the UI in this
run. Component B builds on Component A, so finish A.1 to A.3 first; the B checks set up their
users and workspaces through Component A routes. If `/app` is empty, first build the smallest cluster that answers
`GET /api/health` on ports `8000`, `8001`, and `8002`, then start stage A.1. If `/app` already
has code, improve it, do not start over.

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

1. Read `/spec/spec_a1.md`. Read the other stage specs only when you reach them.
2. Run `check_a1.sh` to see what already passes.
3. **Stage A.1:** fix one failing rule at a time, lowest number first, and rerun `check_a1.sh` after
   every change. Stop when it prints `RESULT: 28 passed, 0 failed`.
4. **Stage A.2:** read `spec_a2.md`, run `check_a2.sh`, fix one failing rule at a time. After every
   change run `check_a2.sh` and `check_a1.sh`. Stop at `RESULT: 20 passed, 0 failed`.
5. **Stage A.3:** read `spec_a3.md`, run `check_a3.sh`, fix one failing rule at a time. After every
   change run `check_a3.sh`, `check_a2.sh` and `check_a1.sh`. Stop at `RESULT: 30 passed, 0 failed`.
6. **Stages B.1 to B.4:** do the same, one at a time, in order. In stage B.n, after every change run
   `check_b<n>.sh`, the stage just before it (`check_b<n-1>.sh`; in B.1 that is `check_a3.sh`) and
   `check_a1.sh`, which covers the shared base (auth, workspaces, channels, request parsing). Stop
   each stage at its `RESULT: N passed, 0 failed` line from the table.
   **Full regression pass:** when a stage first reaches its passing line, run every earlier stage check
   once, one command each, before you start the next stage. Fix any regression before moving on. Also
   run all stage checks once more after your last code change, before you submit.
7. If a rule that passed starts failing, undo your last change before doing anything else.
8. When all seven stages are green, run `check_boot.sh` (conditions 1-3).
9. Run `check_boot.sh --kill` last (condition 4). It kills the node on port `8001` and may leave it down.
10. `check_boot.sh --kill` now restarts the cluster itself if node `1` does not come back. Still
   confirm all three nodes answer `/api/health` again. If node `1` did not come
   back, stop the processes on `8000`, `8001`, and `8002` by PID (find them with `ss -ltnp`, or use
   the PID you saved when you started `start.sh`), start `/app/start.sh` again in the background,
   and confirm `200` on all three ports.

A line that starts with `FAIL SETUP` means a step the later rules depend on failed (for example,
registering a user or creating an invitation). Fix that first, because many rules fail as a side effect.
Stage A.2 and A.3 checks use features from earlier stages to set up their users, so a broken earlier
stage makes later stages fail too.

## How to verify

Run each of these as its own, separate command. Do NOT chain several checks in one command: each
can take about 20 seconds, and your shell kills any command that runs longer than 30 seconds.

```bash
bash /spec/checks/check_a1.sh
bash /spec/checks/check_a2.sh
bash /spec/checks/check_a3.sh
bash /spec/checks/check_b1.sh
bash /spec/checks/check_b2.sh
bash /spec/checks/check_b3.sh
bash /spec/checks/check_b4.sh
bash /spec/checks/check_boot.sh
bash /spec/checks/check_boot.sh --kill
```

Each prints one line per rule or condition, then a final `RESULT:` summary. A rule line looks like
`PASS A1.3 username with a space` or `FAIL A2.6 admin changes another admin: expected 403, got 200`.

- `check_boot.sh` can take close to the 30 second limit. If your shell reports a timeout but the
  output already shows its `RESULT:` line, that line is valid: do not rerun it just because of the
  timeout message. Run it as its own command, with nothing else chained to it.
- Each check starts `/app/start.sh` itself if the cluster is not answering. Your shell kills any
  command that runs longer than 30 seconds, so each check stops sending requests after about 20
  seconds and still prints its summary. If you see `got 000` failures and a NOTE about the time
  limit, some route is slow or hangs (no answer within 3 seconds): fix the first `got 000` first.
- The checks use new usernames and slugs every run, so you can run them as often as you like.
- After you change code, restart the cluster before checking again: find the process that owns each
  port and stop it by PID. Never stop processes by name.

## Before you submit

You may submit only when all of these are true, from output you actually saw **after your last
code change** (if you change code after a check, run all the checks again, stage by stage, one command each):

- the last line of each stage check matches the table above (A.1: 28, A.2: 20, A.3: 30,
  B.1: 22, B.2: 29, B.3: 18, B.4: 24 passed, 0 failed);
- `check_boot.sh --kill` ended with `RESULT: 4 passed, 0 failed`;
- after that, `GET /api/health` returns `200` on ports `8000`, `8001`, and `8002`.

These checks are necessary, not sufficient. The full task in the grading contract still applies.