# Milestone 1 report

- Name: Jessica Pinto
- UNI: jp4908

All results are from my own checks. 

## What I am submitting

- `milestone-1-specs/single-file/`: the **single-file final files** (README, `spec_a.md` with 78 rules, `spec_b.md` with 93 rules, `check_a.sh`, `check_b.sh`, `check_boot.sh`). They are the files used in the last run of Approach 1 (run 5).
- `milestone-1-specs/staged/`: the **staged final files** (full seven-stage README, `spec_a1.md` to `spec_a3.md`, `spec_b1.md` to `spec_b4.md`, `check_a1.sh` to `check_b4.sh`, `check_boot.sh`). They are the files used in the last run of Approach 3 (run 3).
- The final single-file and staged files are therefore taken from the **Approach 1 and Approach 3 updates**. Approaches 2 and 4 were run earlier, with **older increments** of these files; each run block below says which version it used. The same final single-file files are the "final" files for Approach 2, and the same final staged files are the "final" files for Approach 4, but those two approaches were not rerun on the final files.

### File versions used across the runs

- **Single-file v1 (earlier, Approaches 1 and 2):** `spec_a.md` 67 rules (later 72), `spec_b.md` 65 to 72 rules, early README, no API-conventions rules.
- **Single-file v2 (Approach 1, runs 1 to 3):** `spec_a.md` 72 then 78 rules (conventions A73 to A78), `spec_b.md` 72 then 78 rules (conventions B73 to B78), README with the storage section, the "run checks as separate commands" rule and the "rerun both checks after every change" rule.
- **Single-file final (Approach 1, runs 4 and 5):** `spec_a.md` 78 rules (with clarifying sentences on rules 26 and 71), `spec_b.md` 93 rules (edit, delete and reactions B79 to B89 and their events B90 to B93), `check_a.sh`, `check_b.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the combined README. Run 4 used the README before the extended storage paragraph; run 5 used the final README with it (error paths must roll back or commit and send `Content-Length`; WebSocket subscribers must receive events from requests on any node).
- **Staged v1 (earlier, Approach 4 A-alone runs 1 and 2):** stage specs and checks A.1 to A.3 (28, 20, 30), original `check_boot.sh` (run 1) and the restart version (run 2), A-only README.
- **Staged final (Approaches 3 and 4):** A.1 28, A.2 20, A.3 30 (with clarifying sentences on A1.27 and A3.18), B.1 22, B.2 29 (adds edit, delete and reactions), B.3 18, B.4 24 (adds the edit, delete and reaction events), `check_boot.sh` that restarts the cluster after `--kill`, and the full seven-stage README. Approach 3 run 1 used a README that reran every earlier stage after every change, run 2 used the reduced rerun rule (current stage, previous stage and `check_a1.sh` after each change; a full pass when a stage first passes and before submitting), and run 3 added the extended storage paragraph.
- The A-only and B-only READMEs used in Approach 4 are working copies and are not part of the submission.

---

## Approach 1: single-file, A and B together, one run per attempt

**Files submitted (final):** `single-file/` with `README.md` (combined A, B and boot work order, B counted as 93), `spec_a.md` (78 rules), `spec_b.md` (93 rules), `checks/check_a.sh`, `checks/check_b.sh` (93 rules), `checks/check_boot.sh`. **Run 5 used exactly these files.**

**What was run:**
- Runs 1 and 2 (`RG8YfEA`, `KSjbFNK`): earlier increments, 72-rule A and 72-rule B specs. A 72/0, B 72/0, boot 4/4.
- Run 3 (`JEtefGL`): 78-rule A and 78-rule B with the updated README. A 78/0, B 78/0, boot 4/4.
- Run 4 (`NCfAHnG`): 78-rule A, 93-rule B (the final specs), README before the extended storage paragraph. A 78/0, B 93/0, boot 4/4.
- Run 5 (`wAFyWBv`): the final files and the final README. A 78/0, B 93/0, boot 4/4. **This is the final `/app` for Approach 1.**

#### Run 1

- Component: both (A and B in one run)
- Spec and checks version: single-file v1/v2 at the time. `spec_a.md` (72 rules) and `spec_b.md` (72 rules), `check_a.sh`, `check_b.sh` (v3, sequential B34 posts) and `check_boot.sh` in `checks/`. README was the combined version from before the storage section, the "run checks separately" rule, and the "rerun both checks after every change" rule were added.
- Job folder: `jobs/2026-10-27__01-15-36/`   (session `final_task__RG8YfEA__agent`)
- My check results:
  - A: 5 passed, 97 failed at first (cluster failing); 9 passed, 93 failed; 17 passed, 73 failed; 57 passed, 15 failed; 71 passed, 1 failed (rule 71); final **72 passed, 0 failed**
  - B: 30 passed, 42 failed at first; 49/23; 59/13; 65/7; final **72 passed, 0 failed**
  - Boot: `check_boot.sh` 3 passed, 0 failed; `--kill` **4 passed, 0 failed**
- Problems:
  - The agent ran both checks in one command at step 17 and hit the harness's 30-second limit.
  - From steps 18 to 37 the first SQLite design locked up under cross-node writes (many `got 000`). It fixed this with WAL mode and autocommit, which cost about 20 steps.
  - A reached 72/72 at step 49, then B took until step 66. The slowest B rules were the events and mention-count rules (B52, B56 to B60, B63).
  - The agent never reran `check_a.sh` after its B changes. It submitted on the earlier A result ("Component A was previously verified"), so A on the final code was not confirmed.
  - Node 1 came back on its own after the kill check. Finished in 69 steps.

#### Run 2

- Component: both (A and B in one run)
- Spec and checks version: single-file v1/v2 at the time. `spec_a.md` (72 rules) and `spec_b.md` (72 rules), `check_a.sh`, `check_b.sh` (v3) and `check_boot.sh` in `checks/`. README updated after run 1: added the "Storage: three processes share one state" section, the rule to run the checks as separate commands, the rule to rerun both checks after every change, and the rule to submit only on output seen after the last code change.
- Job folder: `jobs/2026-10-27__01-30-28/`   (session `final_task__KSjbFNK__agent`)
- My check results:
  - A: 61 passed, 11 failed at first (rules 31, 50 to 52, 63 to 67, 69 to 71); final **72 passed, 0 failed**
  - B: 22 passed, 52 failed at first; 56/16; 71 passed, 1 failed (B52, mention count) twice; final **72 passed, 0 failed**
  - Boot: `check_boot.sh` 3 passed, 0 failed; `--kill` **4 passed, 0 failed**
- Problems:
  - The agent built A first, reaching 72/72 at step 11, then B. No database lockups or `got 000` stretches, and no 30-second command timeouts (the README fixes from run 1 worked).
  - B52 failed twice because the agent counted mentions on the user profile instead of in `read_state.mention_count`. It found the correct field by reading `check_b.sh`.
  - After its last code change (a read-state fix at step 24) it reran only `check_b.sh`. Its last `check_a.sh` was before that change, so A on the final code was not confirmed, although the change was small.
  - Node 1 did not come back after the kill test, and the agent restarted the cluster and confirmed health on all three ports. Finished in 29 steps, compared with 69 in run 1.

#### Run 3

- Component: both (A and B in one run)
- Spec and checks version: single-file v2, with the API convention rules. `spec_a.md` (78 rules) and `spec_b.md` (78 rules, B52 and B53 reworded to `read_state.mention_count`), `check_a.sh`, `check_b.sh` (78 rules each) and `check_boot.sh` in `checks/`. README with the storage section, the "run checks as separate commands" rule, and the "rerun both checks after every change" rule.
- Job folder: `jobs/2026-10-27__09-27-59/`   (session `final_task__JEtefGL__agent`)
- My check results:
  - A: 59 passed, 19 failed at first; 69/9 (rules 33, 35, 36, 40, 43, 45, 52, 59, 76 still failing); final **78 passed, 0 failed**
  - B: 5 passed, 101 failed at first; 10/91; 50/28; 47/40; 66/12; 73/5; 77 passed, 1 failed (B52); final **78 passed, 0 failed**
  - Boot: `check_boot.sh` 3 passed, 0 failed; `--kill` **4 passed, 0 failed**
- Problems:
  - A reached 78/78 at step 18, then B took until step 37. Finished in 42 steps, compared with 29 in run 2 and 69 in run 1, and the cost was about $0.46.
  - Among the convention rules, A76 (missing fields return 400) stayed failing until A was finished, and B75 (unknown ids return 404) was failing for the early B runs. The agent had put the message `replies` and `pin` routes inside channel routing instead of at the top level, so the 404 and other B rules failed until it fixed that at step 30.
  - Cross-node event rules (B62, B63) took until step 33. The agent switched event delivery to polling the shared store so a write on any node is broadcast to WebSocket subscribers on every node.
  - B52 failed again: the agent first counted mentions on the user profile. Even with the reworded rule, it read the check script to find that `read_state.mention_count` was the field to fill, and fixed that at step 36.
  - After its last code change (the read-state fix), the agent reran `check_a.sh` first (78/0) and then `check_b.sh` (78/0), which is what the README asks for, so A on the final code was confirmed.
  - Node 1 did not come back after the kill test, so the agent restarted the cluster and confirmed health on all three ports.

#### Run 4

- Component: both
- Spec and checks version: single-file final specs: `spec_a.md` (78 rules, with clarifying sentences on rules 26 and 71), `spec_b.md` (93 rules, includes edit/delete/reactions B79 to B89 and their events B90 to B93), `check_a.sh`, `check_b.sh`, `check_boot.sh`, and the combined `README.md` (counts 78 and 93) **before** the extended storage paragraph was added.
- Job folder: `jobs/2026-10-27__12-37-22/`   (session `final_task__NCfAHnG__agent`)
- My check results: A 78 passed, 0 failed; B 93 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 61 steps, about $0.74.
- Problems:
  - `/app` was empty at the start.
  - The first request after startup hung (`got 000`, first check run 2 passed, 108 failed). Causes: a SQLite write lock left open by the duplicate-registration error path, and missing response framing on error responses (keep-alive). Roughly 25 steps went on diagnosing and fixing this.
  - After the first successful runs, A had 58/20 and then 74/4, 77/1, 78/0 and B 66/27, 80/13, 88/5, 92/1, 93/0 before the final results.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.
  - Follow-up: added the extended storage paragraph to the README (error paths, `Content-Length`, cross-node WebSocket fan-out).

#### Run 5 (final files and final README)

- Component: both
- Spec and checks version: single-file final: `spec_a.md` (78 rules, with clarifying sentences on rules 26 and 71), `spec_b.md` (93 rules, includes edit/delete/reactions B79 to B89 and their events B90 to B93), `check_a.sh`, `check_b.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the combined `README.md` with the extended storage paragraph (error paths must roll back or commit and send `Content-Length`; WebSocket subscribers must receive events from requests on any node). **These are the files submitted.**
- Job folder: `jobs/2026-10-27__12-57-41/`   (session `final_task__wAFyWBv__agent`)
- My check results: A 78 passed, 0 failed; B 93 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 43 steps, about $0.43.
- Problems:
  - `/app` was empty at the start. The first full check runs were 19 passed, 79 failed (A) and then 40 passed, 53 failed (B) while the first server crashed or hung on login (`got 000`).
  - Progress went through A 75/3 and 76/2, and B 70/23, 79/14, 92/1 and 93/0.
  - After the last B changes, A dropped to 64 passed, 16 failed (a regression from the B work); the agent fixed it and reran both checks to 78/0 and 93/0.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.

### Final `/app` (Approach 1)

- Job folder: `jobs/2026-10-27__12-57-41/` (run 5)
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_a.sh` (Component A) | 78 | 0 | A regression (64/16) after late B changes was fixed before submitting. |
  | `check_b.sh` (Component B) | 93 | 0 | Includes edit, delete, reactions and their events (B79 to B93). |
  | `check_boot.sh` (milestone 0) | 4 | 0 | 3/0, then `--kill` 4/0; the script restarted the cluster after node 1 stayed down. |

---

## Approach 2: single-file, A alone (iterate, freeze), then B

**Files submitted (final):** the same `single-file/` files as Approach 1.

**What was run:** earlier increments of the single-file files, **not** the final ones. A alone was run first (runs 1 to 4), then B alone (runs 5 to 8).
- A alone: `ce7xyHH`, `NwZKf3t`, `syzmgKT` (67-rule A, the last with the boot check), `dYT22WQ` (72-rule A, 72/0, boot 4/4).
- B alone: `QnBukKe` (65 passed, B62 and B63 missing from the output), `iYVJPPM`, `qnsCQcY` (67-rule B, 67/0), `GbyvWpe` (72-rule B, 72/0, boot 4/4).

The FINAL single-file files (78 and 93 rules) were not run as separate A-alone and B-alone runs; they were run together in Approach 1. These runs show how the specs and checks developed from 67 to 72 rules for each component, and how the check scripts were fixed along the way.

#### Run 1

- Component: A
- Spec and checks version: single-file, A only; 67 rules; first `check_a.sh` with a 20 s time budget; no `check_boot.sh`
- Job folder: `jobs/2026-10-26__17-58-44/`   (session `final_task__ce7xyHH__agent`)
- Agent steps and cost: 10 steps, about $0.04
- My check results: 66 passed, 1 failed; 66 passed, 1 failed; final 67 passed, 0 failed
- Problems: rule 31 (admin vs admin) returned 200, then rule 33 (role "owner" via PATCH) returned 403 instead of 400, because the permission check ran before the invalid-role check; both fixed. Two agent commands hit the harness's 30 s limit. After the final pass no cluster was running, so the agent restarted it and confirmed health on all three ports. No boot check.

#### Run 2

- Component: A
- Spec and checks version: single-file, A only; 67 rules; `check_a.sh` with time budget; `check_boot.sh` was listed but not in the mounted `/spec/checks/`
- Job folder: `jobs/2026-10-26__21-42-22/`   (session `final_task__NwZKf3t__agent`)
- Agent steps and cost: 17 steps, about $0.09
- My check results: 43 passed, 32 failed; 43/32; 47/28; final 67 passed, 0 failed
- Problems: the early failures were mostly a partly built or hung server (many `got 000`). The agent fixed these, then reached 67/0. It verified the boot conditions by hand because `check_boot.sh` was not where the README said.

#### Run 3

- Component: A
- Spec and checks version: single-file, A only; 67 rules; README with the four boot conditions; `check_boot.sh` mounted at `/spec/check_boot.sh`, not `/spec/checks/`
- Job folder: `jobs/2026-10-26__22-08-57/`   (session `final_task__syzmgKT__agent`)
- Agent steps and cost: 19 steps, about $0.11
- My check results: 15 passed, 65 failed; 7 passed, 84 failed; final 67 passed, 0 failed; boot 3 passed, 0 failed, then `--kill` 4 passed, 0 failed
- Problems: the agent's first SQLite design locked up under requests (status 000 for many rules), and it switched to WAL and autocommit. Boot check was at the wrong path, so its first call to `/spec/checks/check_boot.sh` failed (return code 127). After `--kill`, node 1 did not come back, so the agent restarted all three nodes and confirmed health.

#### Run 4

- Component: A (the agent also built B routes and a UI shell on its own)
- Spec and checks version: single-file, A only; 72 rules (new rules 68 to 72); `check_boot.sh` in `checks/`
- Job folder: `jobs/2026-10-26__22-35-41/`   (session `final_task__dYT22WQ__agent`)
- Agent steps and cost: 19 steps, about $0.12
- My check results: 44 passed, 42 failed; 44/42; 71 passed, 1 failed (rule 71); final 72 passed, 0 failed; boot 3/0, then `--kill` 4 passed, 0 failed
- Problems: the new rule 71 (targeted invitation single-use) caught a real gap and was fixed. After A was green, the agent went out of scope: no `check_b.sh` or `spec_b.md` was mounted, so it added B routes and a UI shell on its own. Node 1 did not respawn after `--kill`; the agent restarted the cluster by PID.

#### Run 5

- Component: B
- Spec and checks version: single-file, B only; 67 rules; first `check_b.sh` (time budget 17 s; B62 and B63 could be silently skipped); no `check_boot.sh`
- Job folder: `jobs/2026-10-26__22-46-04/`   (session `final_task__QnBukKe__agent`)
- Agent steps and cost: 59 steps, about $0.69
- My check results: 55 passed, 10 failed; 60/5; 63/2; 62/3; final 65 passed, 0 failed (only 65 of 67 rules reported)
- Problems: B62 and B63 never appeared in the output because the WebSocket helper hit its 8 s limit and the rules were dropped, so the 65/0 is not a full pass. A `$3: unbound variable` crash in the check after a setup failure. Several late rules (B52, B53, B64 to B66) failed with `got 000` because the time budget ran out. Boot checked by hand. After B passed, the agent started adding A routes on its own.

#### Run 6

- Component: B
- Spec and checks version: single-file, B only; 67 rules; `check_b.sh` v2 (WebSocket checks in the background; B34 used 55 parallel posts); no `check_boot.sh`
- Job folder: `jobs/2026-10-26__23-02-46/`   (session `final_task__iYVJPPM__agent`)
- Agent steps and cost: 41 steps, about $0.38
- My check results: 31 passed, 43 failed; 64/3; 66/1 (B34); 36/42; 66/1; final 67 passed, 0 failed
- Problems: B34 failed in most runs because my parallel-post change flooded the agent's server and lost messages (16 to 46 of 55 arrived); the agent spent about 25 steps on throughput and moved state to `/dev/shm`. This was a check artifact. Two runs show 0 passed (cluster not up). Boot checked by hand.

#### Run 7

- Component: B
- Spec and checks version: single-file, B only; 67 rules; `check_b.sh` v3 (B34 posts sequentially); no `check_boot.sh`
- Job folder: `jobs/2026-10-26__23-11-40/`   (session `final_task__qnsCQcY__agent`)
- Agent steps and cost: 52 steps, about $0.63
- My check results: 49 passed, 18 failed; 57/10; 59/8; 66/1 (B63); final 67 passed, 0 failed
- Problems: event-ordering rules (B55 to B63) took the most iterations. Two "0 passed" runs were just the cluster not being up. Boot checked by hand; after B passed, the agent also exercised A routes itself.

#### Run 8

- Component: B
- Spec and checks version: single-file, B only; 72 rules (new B68 to B72); `check_b.sh` v3 with B68 to B72; `check_boot.sh` in `checks/`
- Job folder: `jobs/2026-10-26__23-29-37/`   (session `final_task__GbyvWpe__agent`)
- Agent steps and cost: 29 steps, about $0.22
- My check results: 58 passed, 16 failed; final 72 passed, 0 failed; boot 3 passed, 0 failed, then `--kill` 4 passed, 0 failed
- Problems: the early failures (4/92, 3/93) were the cluster not yet built. Node 1 did not come back after `--kill`, so the agent restarted the cluster and confirmed health on all three ports.

### Final `/app` (Approach 2)

- Job folders: A: `jobs/2026-10-26__22-35-41/` (run 4); B: `jobs/2026-10-26__23-29-37/` (run 8). These were run on the earlier 72-rule files, not on the final single-file files.
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_a.sh` (Component A, 72 rules) | 72 | 0 | Run 4; boot 4/4 in the same run. |
  | `check_b.sh` (Component B, 72 rules) | 72 | 0 | Run 8; boot 4/4 in the same run. |
  | `check_boot.sh` (milestone 0) | 4 | 0 | Both runs: 3/0, then `--kill` 4/0; node 1 did not come back and the agent restarted the cluster. |

---

## Approach 3: staged, A and B together

**Files submitted (final):** `staged/` with the full seven-stage `README.md`, `spec_a1.md` to `spec_a3.md`, `spec_b1.md` to `spec_b4.md`, `checks/check_a1.sh` to `check_b4.sh`, and `checks/check_boot.sh` (restarts the cluster after `--kill`). **Run 3 used exactly these files.**

**What was run:**
- Run 1 (`27Hx9aT`): staged final specs and checks, README with "rerun every earlier stage after every change". All 7 stages and boot passed; 124 steps, $1.81.
- Run 2 (`Cg2Phqs`): the same specs and checks, README with the reduced rerun rule. All 7 stages and boot passed; 107 steps, $1.45.
- Run 3 (`C5KDYbP`): the same specs and checks, README with the reduced rerun rule and the extended storage paragraph. All 7 stages and boot passed; 41 steps, $0.45. **This is the final `/app` for Approach 3.**

#### Run 1

- Component: both
- Spec and checks version: staged layout, final specs and checks. Stage specs `spec_a1.md` (28 rules), `spec_a2.md` (20), `spec_a3.md` (30), `spec_b1.md` (22), `spec_b2.md` (29, includes edit/delete/reactions), `spec_b3.md` (18), `spec_b4.md` (24, includes the edit/delete/reaction events), with `check_a1.sh` to `check_b4.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the full seven-stage `README.md` (rerun every earlier stage after every change).
- Job folder: `jobs/2026-10-27__11-48-41/`   (session `final_task__27Hx9aT__agent`)
- My check results: A.1 28 passed, 0 failed; A.2 20 passed, 0 failed; A.3 30 passed, 0 failed; B.1 22 passed, 0 failed; B.2 29 passed, 0 failed; B.3 18 passed, 0 failed; B.4 24 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 124 steps, about $1.81.
- Problems:
  - Cost: 67 of the 124 steps were check runs, because the README required rerunning every earlier stage after every change. No earlier stage regressed in this run, so most of those reruns found nothing.
  - `/app` was empty at the start, and the agent first needed several steps to get the cluster to start (a crash on every request, then nodes exiting).
  - A.3 first run was 29 passed, 1 failed; B.3 (pins, mentions, search) and B.4 (WebSocket events, cross-node fan-out and resume) each took several fix rounds.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.
  - Follow-up: changed the README so that after each change only the current stage and the previous stage are rerun, with a full regression pass once per stage and once before submitting.

#### Run 2

- Component: both
- Spec and checks version: staged layout, final specs and checks, as in run 1, with the full seven-stage `README.md` with the reduced rerun rule (after each change run the current stage, the previous stage and `check_a1.sh`; a full regression pass when a stage first passes and before submitting).
- Job folder: `jobs/2026-10-27__12-17-43/`   (session `final_task__Cg2Phqs__agent`)
- My check results: A.1 28 passed, 0 failed; A.2 20 passed, 0 failed; A.3 30 passed, 0 failed; B.1 22 passed, 0 failed; B.2 29 passed, 0 failed; B.3 18 passed, 0 failed; B.4 24 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 107 steps, about $1.45.
- Problems:
  - `/app` was empty at the start, so the agent built Components A and B from the task description.
  - B.4 (WebSocket events, cross-node fan-out and resume) took the most rounds: first run 6 passed, 19 failed, then 17/7 and 21/3 before reaching 24/24.
  - After the last B.4 changes, the final full check pass showed A.3 at 27 passed, 3 failed: a regression in an earlier stage. The agent fixed it and finished with every stage green.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.
  - Follow-up: added the extended storage paragraph to the README (error paths, `Content-Length`, cross-node WebSocket fan-out).

#### Run 3 (final files and final README)

- Component: both
- Spec and checks version: staged final: `spec_a1.md` (28 rules), `spec_a2.md` (20), `spec_a3.md` (30), `spec_b1.md` (22), `spec_b2.md` (29, includes edit/delete/reactions), `spec_b3.md` (18), `spec_b4.md` (24, includes the edit/delete/reaction events), with `check_a1.sh` to `check_b4.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the full seven-stage `README.md` with the reduced rerun rule and the extended storage paragraph (error paths must roll back or commit and send `Content-Length`; WebSocket subscribers must receive events from requests on any node). **These are the files submitted.**
- Job folder: `jobs/2026-10-27__13-10-11/`   (session `final_task__C5KDYbP__agent`)
- My check results: A.1 28 passed, 0 failed; A.2 20 passed, 0 failed; A.3 30 passed, 0 failed; B.1 22 passed, 0 failed; B.2 29 passed, 0 failed; B.3 18 passed, 0 failed; B.4 24 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 41 steps, about $0.45.
- Problems:
  - `/app` was empty at the start; the agent built Components A and B from the task description. The first A.1 check timed out at 30 seconds before the cluster was ready.
  - B.1 first run was 9 passed, 13 failed; B.3 first run was 0 passed, 37 failed (cluster not answering), then 27/2; B.4 went 6/20, 7/19 and 13/11 before 23/1 and 24/0.
  - After the last code change, the agent reran A.1 to A.3 and B.1 to B.3 once each; all stayed green.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.

### Final `/app` (Approach 3)

- Job folder: `jobs/2026-10-27__13-10-11/` (run 3)
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_a1.sh` (auth, workspaces, channels, open join) | 28 | 0 | |
  | `check_a2.sh` (roles, channel permissions, ownership transfer) | 20 | 0 | |
  | `check_a3.sh` (invitations, API conventions, join mode) | 30 | 0 | |
  | `check_b1.sh` (channel management) | 22 | 0 | |
  | `check_b2.sh` (messages, paging, edit, delete, reactions) | 29 | 0 | |
  | `check_b3.sh` (search, pins, mentions) | 18 | 0 | |
  | `check_b4.sh` (events, read state, API conventions) | 24 | 0 | WebSocket events took the most rounds. |
  | `check_boot.sh` (milestone 0) | 4 | 0 | 3/0, then `--kill` 4/0; the script restarted the cluster after node 1 stayed down. |

---

## Approach 4: staged, A alone (iterate, freeze), then B

**Files submitted (final):** the same `staged/` files as Approach 3.

**What was run:** A alone (runs 1 to 3), then B alone (runs 4 to 6), with **older increments** of the staged files and trimmed working READMEs (A-only and B-only), not the submitted full README. The final staged files were run together in Approach 3, not in Approach 4.
- A alone (staged A.1 to A.3 with an A-only README):
  - Run 1 (`ZvfouQZ`): original `check_boot.sh` and README (19 steps, A 28/20/30, boot 4/4).
  - Run 2 (`yG2qgTr`): `check_boot.sh` that restarts the cluster, README note about the boot-check timeout (32 steps, 28/20/30, boot 4/4).
  - Run 3 (`r5pscy5`): the same plus clarifying sentences on A1.27 and A3.18 (55 steps, 28/20/30, boot 4/4). **Frozen as staged A.**
- B alone (B.1 to B.4 with a B-only README; `/app` started empty, so the agent also built the A routes):
  - Run 4 (`ULSj7DQ`): B.2 was the earlier 18-rule version; B.1 22, B.2 18, B.3 18, B.4 24, boot 4/4.
  - Run 5 (`JkvrJMA`): B.2 at 29 rules; `check_b4.sh` was a copy of the B.3 check, so B.4 was not run; boot 4/4.
  - Run 6 (`mzu6yqm`): all final B files, B.1 22, B.2 29, B.3 18, B.4 24, boot 4/4 (52 steps, $0.49). **Frozen as staged B.**

#### Run 1 (A alone)

- Component: A
- Spec and checks version: staged layout, A-only. Stage specs `spec_a1.md` (28 rules), `spec_a2.md` (20), `spec_a3.md` (30), with `check_a1.sh`, `check_a2.sh`, `check_a3.sh`, `check_boot.sh` (original, no cluster restart), and the A-only `README.md` (stages A.1 to A.3, then boot).
- Job folder: `jobs/2026-10-27__09-56-31/`   (session `final_task__ZvfouQZ__agent`)
- My check results: A.1 28 passed, 0 failed; A.2 20 passed, 0 failed; A.3 30 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 19 steps, about $0.10.
- Problems:
  - A.1 first run was 27 passed, 1 failed: A1.27 (joining a private channel without an invite returned 200, expected 403). Fixed in one step.
  - A.3 first run was 29 passed, 1 failed: A3.18 (a targeted invitation could be accepted twice, got 200 then 200, expected 200 then 404). Fixed in one step.
  - `check_boot.sh` and `check_boot.sh --kill` both hit the agent's 30 second command limit, although their output (including the `RESULT:` line) still came back.
  - After `--kill`, all three ports were down even though the check reported that node 1 came back. The agent restarted `/app/start.sh` by hand and confirmed 200 on 8000, 8001 and 8002 before submitting.
  - Follow-up: I changed `check_boot.sh` to restart the cluster itself after `--kill` if node 1 stays down, and added a README note that a `RESULT:` line is valid even if the shell reports a timeout.

#### Run 2 (A alone)

- Component: A
- Spec and checks version: staged layout, A-only. Stage specs `spec_a1.md` (28 rules), `spec_a2.md` (20), `spec_a3.md` (30), with `check_a1.sh`, `check_a2.sh`, `check_a3.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the A-only `README.md`.
- Job folder: `jobs/2026-10-27__10-07-28/`   (session `final_task__yG2qgTr__agent`)
- My check results: A.1 28 passed, 0 failed; A.2 20 passed, 0 failed; A.3 30 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 32 steps, about $0.22.
- Problems:
  - A.1 first run was 27 passed, 1 failed: A1.27 (private channel joined without an invite returned 200, expected 403). Same failure as run 1. Fixed in one step.
  - A.2 first run was 2 passed, 20 failed: the role and ownership-transfer routes did not exist yet (404). The agent added them and reached 20/20.
  - A.3 first run was 24 passed, 6 failed: A3.18 (targeted invitation could be accepted twice, same as run 1) and the join-mode rules A3.25 to A3.29, which were not built yet. All fixed.
  - `check_boot.sh --kill` still reported a 30 second timeout although its full output, including `RESULT: 4 passed, 0 failed`, came back. The new restart logic worked: node 1 stayed down and the script restarted the cluster, and all three ports answered.
  - Changes made after this run: added a sentence to A1.27 (open join is for public channels only) and A3.18 (a targeted invitation has exactly one use).

#### Run 3 (A alone)

- Component: A
- Spec and checks version: staged layout, A-only. Stage specs `spec_a1.md` (28 rules, with added wording that open join applies to public channels only), `spec_a2.md` (20), `spec_a3.md` (30, with added wording that a targeted invitation has exactly one use), with `check_a1.sh`, `check_a2.sh`, `check_a3.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the A-only `README.md`.
- Job folder: `jobs/2026-10-27__10-25-35/`   (session `final_task__r5pscy5__agent`)
- My check results: A.1 28 passed, 0 failed; A.2 20 passed, 0 failed; A.3 30 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 55 steps, about $0.46.
- Problems:
  - A1.27 failed on the first build (27 passed, 1 failed), as in earlier runs; fixed in one step. A3.18 passed on the first build this time.
  - A3.23 (malformed JSON returns 400) was the main cost. The agent's guard for malformed bodies was placed in the GET handler three times, which crashed every GET route (`got 000` on A1.15, A1.19 to A1.22, A1.28). Rerunning A.1 and A.2 after each change caught each regression; the agent fixed them and finished at 28/28, 20/20 and 30/30.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output, including `RESULT: 4 passed, 0 failed`, came back. Node 1 stayed down and the script restarted the cluster; all three ports answered.

#### Run 4 (B alone)

- Component: B
- Spec and checks version: staged layout, B-only. Stage specs `spec_b1.md` (22 rules), `spec_b2.md` (18 rules, earlier version without edit/delete/reactions), `spec_b3.md` (18), `spec_b4.md` (24, includes the edit/delete/reaction events), with `check_b1.sh` to `check_b4.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the B-only `README.md`.
- Job folder: `jobs/2026-10-27__10-40-47/`   (session `final_task__ULSj7DQ__agent`)
- My check results: B.1 22 passed, 0 failed; B.2 18 passed, 0 failed; B.3 18 passed, 0 failed; B.4 24 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 29 steps, about $0.22.
- Problems:
  - `/app` was empty at the start, so the agent built the Component A routes the B checks need as well as Component B. My README had said A was already built.
  - The B.2 files in this run were the earlier 18-rule version, so the edit, delete and reaction rules (B2.19 to B2.29) were not tested; the README said 29 and the agent did not flag the mismatch. B.4, with its edit/delete/reaction event rules, was the new 24-rule version and passed.
  - The launcher failed to start on the first attempt (step 7) and was fixed from its log.
  - First check runs had failures in B2.9, B3.3, B3.15, B4.17, B4.19 and the WebSocket events; all were fixed.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.
  - Follow-up: corrected the B-only README to say the agent must build the Component A routes if `/app` is empty.

#### Run 5 (B alone)

- Component: B
- Spec and checks version: staged layout, B-only. Stage specs `spec_b1.md` (22 rules), `spec_b2.md` (29, includes edit/delete/reactions), `spec_b3.md` (18), `spec_b4.md` (24), with `check_b1.sh`, `check_b2.sh`, `check_b3.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the corrected B-only `README.md`. `checks/check_b4.sh` in this run was a copy of the B.3 check by mistake.
- Job folder: `jobs/2026-10-27__10-51-14/`   (session `final_task__JkvrJMA__agent`)
- My check results: B.1 22 passed, 0 failed; B.2 29 passed, 0 failed; B.3 18 passed, 0 failed; B.4 not run (the file was the B.3 check, which printed 18 passed, 0 failed); boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 39 steps, about $0.30.
- Problems:
  - `/app` was empty at the start, so the agent built the Component A routes the B checks need as well as Component B.
  - `checks/check_b4.sh` was a copy of `check_b3.sh`; the agent noticed, built the B.4 behavior from the spec without a check, and submitted without a B.4 result. The events, read state and API-convention rules were not verified.
  - B.2 first run was 25 passed, 4 failed (B2.9 and the reaction rules B2.27 to B2.29); the reaction failure was an emoji URL-decoding bug in the path, fixed.
  - B.3 first run was 9 passed, 9 failed. A patch left an indentation error that stopped the cluster from booting; fixed. A later read-state change broke the replies cursor (B2.18); fixed and rechecked.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.
  - Follow-up: added `verify_layout.sh` to check that each stage spec and check file has the right rules before a run.

#### Run 6 (B alone, all final B files)

- Component: B
- Spec and checks version: staged layout, B-only, final B files. Stage specs `spec_b1.md` (22 rules), `spec_b2.md` (29, includes edit/delete/reactions), `spec_b3.md` (18), `spec_b4.md` (24, includes the edit/delete/reaction events), with `check_b1.sh` to `check_b4.sh`, `check_boot.sh` (restarts the cluster after `--kill`), and the corrected B-only `README.md`.
- Job folder: `jobs/2026-10-27__11-39-17/`   (session `final_task__mzu6yqm__agent`)
- My check results: B.1 22 passed, 0 failed; B.2 29 passed, 0 failed; B.3 18 passed, 0 failed; B.4 24 passed, 0 failed; boot 4 passed, 0 failed (`check_boot.sh` 3/0, then `--kill` 4/0). The agent submitted after 52 steps, about $0.49.
- Problems:
  - `/app` was empty at the start, so the agent built the Component A routes the B checks need as well as Component B.
  - B.1 first run was 21 passed, 1 failed (B1.19, archived channels hidden unless `include_archived=1`); fixed.
  - B.2 first runs failed on the cursor rules (B2.6 to B2.9) and replies paging (B2.18); fixed.
  - B.3 first run was 6 passed, 12 failed (pins, mentions, search and their paging); fixed over several rounds.
  - B.4 first run was 7 passed, 19 failed: WebSocket subscribe and authentication did not work. After that the agent kept subscribers per process, so B4.9 and B4.10 (events reaching subscribers on every node, and resume on another node) failed until it made each node poll the shared event log.
  - `check_boot.sh --kill` reported a 30 second timeout although its full output came back; node 1 stayed down and the script restarted the cluster.

### Final `/app` (Approach 4)

- Job folders: A phase: `jobs/2026-10-27__10-25-35/` (run 3); B phase: `jobs/2026-10-27__11-39-17/` (run 6). The B phase started from an empty `/app` and built the Component A routes itself, so the A results below come from the A phase and the B results from the B phase.
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_a1.sh` | 28 | 0 | A phase, run 3. |
  | `check_a2.sh` | 20 | 0 | A phase, run 3. |
  | `check_a3.sh` | 30 | 0 | A phase, run 3. |
  | `check_b1.sh` | 22 | 0 | B phase, run 6. |
  | `check_b2.sh` | 29 | 0 | B phase, run 6. |
  | `check_b3.sh` | 18 | 0 | B phase, run 6. |
  | `check_b4.sh` | 24 | 0 | B phase, run 6. |
  | `check_boot.sh` | 4 | 0 | Both phases: 3/0, then `--kill` 4/0; the script restarted the cluster after node 1 stayed down. |

---

## Comparison of the approaches

All four approaches ended with every one of my checks passing, so on my own checks none of them is clearly more correct than another. They differ mostly in cost and in how the failures showed up.

**Single-file (Approaches 1 and 2).** Approach 1's final run (run 5) took 43 steps and about $0.43 for A and B together, after the storage paragraph was added to the README; the earlier runs took 69, 29, 42 and 61 steps. In Approach 2, building A alone first (about 10 to 30 steps) and then B alone was cheap per run and exposed weak rules early (rule 71 in A, and the B62 and B63 check bug), but those runs used earlier 67 and 72 rule files and the agent often drifted into the other component on its own.

**Staged (Approaches 3 and 4).** The stage-by-stage checks localized failures well and made regressions easy to see: in Approach 3 run 2 the last full pass caught an A.3 regression after B.4 changes. The cost depended on the README's rerun rule: rerunning every earlier stage after every change took 124 steps and $1.81, the reduced rule took 107 steps and $1.45, and the reduced rule plus the extended storage paragraph took 41 steps and $0.45, about the same as the single-file layout. In Approach 4, A alone took 19 to 55 steps and B alone 29 to 52 steps; because the B phase started from an empty `/app`, it had to rebuild A, so building A first did not carry over.

**What mattered most.** The biggest costs came from the same few implementation traps, not from the layout: SQLite write locks left open by error paths (which showed up as `got 000` failures), missing response framing on error responses, WebSocket events that did not reach subscribers on other nodes, and edits that broke earlier work. The final README's storage paragraph, which names those traps, cut both layouts' step counts, although I cannot isolate its effect from run-to-run variation. A second lesson was that the checks must be exact: early check bugs (silently dropped rules, a flooding parallel-post test, a wrong check file) cost more agent steps than most real bugs, so I added `verify_layout.sh` and made each check report a missing rule as a failure.

**Limits.** These are my own checks, not the hidden tests, and each approach has only one to eight runs, so run-to-run variation is large. The final single-file and staged files come from the Approach 1 and Approach 3 updates; Approaches 2 and 4 were run on older increments and were not rerun on the final files.