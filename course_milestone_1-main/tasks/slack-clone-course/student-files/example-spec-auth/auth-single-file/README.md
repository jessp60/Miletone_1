# Read this first: work order and rules for this run

This run is about one component: auth (register, login, `me`). Before you run
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, you MUST have run the check below and seen
**all nine** rules pass. Do not submit unless every one of them passes.

The task description you were given is still the grading contract. The files in `/spec` do
not change it; they tell you what to work on now and how to prove it works.

## Files in /spec/auth-single-file

| File | What it is |
|---|---|
| `README.md` | This file: work order, how to verify, when you may submit |
| `auth.md` | The nine rules for auth |
| `checks/check_auth.sh` | Prints PASS or FAIL for each rule in `auth.md` |

## Scope

Work only on the routes in `auth.md`. Do NOT start on messages, files, IRC, or the UI in
this run. If `/app` is empty, first build the smallest cluster that answers `GET /api/health`
on ports `8000`, `8001`, and `8002`, then add auth. If `/app` already has code, improve it,
do not start over.

## Order of work

1. Read `/spec/auth-single-file/auth.md`.
2. Run the check before changing anything, to see what already passes.
3. Fix one failing rule at a time, lowest number first.
4. Run the check again after every change.
5. If a rule that passed starts failing, undo your last change before doing anything else.

## How to verify

Run this as a single command:

```bash
bash /spec/auth-single-file/checks/check_auth.sh
```

It prints one line per rule and a final summary:

```text
PASS 1 register returns 201 with user and token
FAIL 6 login with wrong password: expected 401, got 200
RESULT: 8 passed, 1 failed
```

- The check starts `/app/start.sh` itself if the cluster is not answering, and finishes in
  under 30 seconds.
- After you change code, restart the cluster before checking again: find the process that
  owns each port and stop it by PID. Never stop processes by name.

## Before you submit

You may submit only when both of these are true, from output you actually saw:

- the last line of the check is `RESULT: 9 passed, 0 failed`;
- `GET /api/health` returns `200` on ports `8000`, `8001`, and `8002`.

These nine rules are necessary, not sufficient. The full task in the grading contract still
applies.
