# Writing specs and checks

How to write the specs and checks for milestone 1.

## 1. What a spec is

A spec says what the software must do, in a way that can be checked ("users can upload JPEG or
PNG up to 10MB", not "add photo sharing"). The agent writes the code from your spec. An agent
given a vague request has to guess, and it guesses wrong in ways that look plausible.

## 2. An example spec: auth

> [!WARNING]
> **THIS IS JUST AN EXAMPLE. DO NOT COPY IT INTO YOUR SPEC FOLDER.** Use it to see what a spec
> and its checks can look like. It is one suggestion, not the required format: you may write
> your specs another way, as long as they follow the Single-file and Staged layouts.

A complete example spec for auth (register, login, `me`) is in
[`tasks/slack-clone-course/student-files/example-spec-auth/`](tasks/slack-clone-course/student-files/example-spec-auth/).
The agent does not see it.

| File | What it is |
| --- | --- |
| `auth-single-file/auth.md` | Nine rules, each one request with an exact result |
| `auth-single-file/checks/check_auth.sh` | Tests each rule against the running cluster, prints PASS or FAIL |
| `auth-single-file/README.md` | Written for the agent, not for you: it is the first file the agent reads in `/spec`. It gives the scope, order of work, how to verify, and when the agent may submit |
| `auth-staged/` | The same nine rules as three chained files (n = 3), each with its own check |

<details>
<summary>The <code>instruction.md</code> lines behind <code>auth.md</code></summary>

> `POST /api/auth/register` — body `{username, password, display_name?}`. `username` is a short
> URL-safe slug (letters/digits/underscore, no spaces or special chars). `password` >= 8
> characters. Returns `201 {user, token}` where `token` is a bearer token of length >= 16.
> Duplicate `username` → `409`. Invalid username (e.g. contains a space) or short password →
> `400`. The endpoint takes no email field.

> `POST /api/auth/login` — body `{username, password}`. Returns `200 {user, token}`. Wrong
> password → `401`.

> `GET /api/auth/me` — returns `200 {user}` for the bearer-token holder. Missing or invalid
> token → `401`.

Each rule in `auth.md` takes one behavior from this text and states it so a script can check
it. `checks/check_auth.sh` then sends real requests to the running app for each rule and prints
PASS or FAIL, so the agent can tell which rules its code still gets wrong.

</details>

## 3. The two documents the agent reads

| Document | Who writes it | What it says |
| --- | --- | --- |
| `instruction.md` | Staff (you cannot change it) | The whole Slack clone: every endpoint and status code. |
| Your spec folder (`/spec`) | You | How to get there in 50 minutes: what to build, in what order, how to test it, when to submit. |

`instruction.md` alone is not enough. It is too big for one run, gives no order, and gives the
agent no way to know whether its code works. Left alone, the agent builds a bit of everything,
tests nothing, and submits.

Your specs do not replace `instruction.md`; the agent still has it. Your specs take the
milestone 1 lines listed in [`COMPONENTS.md`](COMPONENTS.md), turn each one into a rule a script
can check, and tell the agent when it may submit.

## 4. What goes in the spec folder

You edit this folder (it starts empty):

```text
tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-1-specs/
```

The run copies it into the agent's container as `/spec`. During a run this folder holds one
layout, with `README.md` at its top level. At submission it holds `single-file/` and `staged/`
(see [README section 3](README.md#3-running-the-agent)). During a run it holds:

- **`README.md`**: the only file the agent is told to read. It must name every other file, and
  state the scope, the order of work, and when the agent may submit. A file your README does not
  mention may never be opened.
- **Spec files**: see [section 5](#5-the-two-layouts).
- **Check scripts**: see [section 6](#6-checks).

Some rules, such as the API conventions in `COMPONENTS.md` (auth headers, status codes, response
envelopes), apply to every endpoint in both components. They are not a component of their own.
You may include them in Spec A and in Spec B, and test them in each component's checks on that
component's own endpoints (for example, a request with no token returns `401`).

> [!TIP]
> **Do not let the agent break milestone 0.** The cluster must still boot and hold the four
> baseline conditions from milestone 0, and the hidden tests still check all four. Consider
> incorporating the conditions from
> [`milestone-0-specs/README.md`](tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-0-specs/README.md)
> into your own `README.md` or checks. A strong spec protects what already works.

## 5. The two layouts

You write each component's spec in two layouts. Both cover the same rules; the only difference
is how they are divided.

- **Single-file** (Approaches 1 and 2): all of a component's rules in one file, with one check.
  The agent builds them in whatever order it chooses.
- **Staged** (Approaches 3 and 4): the same rules split into n ordered steps (A.1 to A.n, B.1 to
  B.n), each with its own check. You choose n. Each step builds on the functions from the step
  before it, and lists the functions it uses and the ones it adds.

Each layout is used in two of the four approaches (full details in
[README section 1](README.md#1-layouts-and-approaches)):

- **Approaches 1 and 3** give the agent Spec A and Spec B together, in the Single-file and Staged
  layouts.
- **Approaches 2 and 4** work on one component at a time, in the Single-file and Staged layouts.

**What should stay fixed:**

- Both layouts cover the same rules. Staged divides them into steps; it does not add or drop any.
- Single-file has one spec file and one check per component.
- Staged has n ordered spec files per component, each with its own check, and each file lists
  the functions it uses from earlier files and the ones it adds.
- `/spec/README.md` is the agent's entry point and names every other file.

**What is up to you:**

- n, and where each step starts and ends
- file and folder names
- function names, and which functions each step adds or reuses
- how each rule is worded, and how finely you break up each line (one line can become several
  rules)
- the order of work, and when the agent may submit
- workflow instructions in your README (for example, run the checks after each step and fix
  failures before moving on)
- warnings about mistakes you have seen the agent make in earlier runs
- time guidance, such as how long to spend on each step
- what your checks test and print, and how they are built (for example, a helper file shared by
  several checks, like `auth-staged/checks/lib.sh` in the auth example)
- what you include beyond the listed lines, such as the milestone 0 conditions
- how your README differs between the two layouts (only the rules have to match)

The spec folder should hold one layout at a time. Keep a copy of each somewhere else and put the one
you want to run in the folder before you start the agent.

Example folder trees for each layout are in [section 7](#7-example-folder-trees).

## 6. Checks

The agent cannot tell whether its own code works. Your checks are how it finds out.

A check is a small script that sends real requests to the running app and prints PASS or FAIL
for each rule. The agent runs it while it works, fixes what fails, and submits only when
everything passes.

- **Your checks** are the practice exam. The agent can run them as often as it likes.
- **The hidden tests** are the real exam. The agent never sees them, and they decide your grade.

The closer your checks are to the real exam, the better the agent scores. A rule with no check
is a rule the agent will probably get wrong without noticing.

Two limits: each agent command is cut off after 30 seconds, so a check that takes longer never
finishes; and the agent has 50 minutes in total.

Your checks run inside the agent's container, against the app it builds, so you cannot fully
test them on your own machine. Before a run, check each script for syntax errors
(for a bash script, `bash -n <your-check>.sh`). After your first run, read the transcript to confirm your
checks ran, finished within 30 seconds, and printed clear PASS or FAIL lines.

<details>
<summary>Tip: keep check output short</summary>

Everything a check prints shows up in the transcript (`agent/mini-swe-agent.txt`), so you can
search it for your check scripts' file names to see each time the agent ran one, and for `PASS`
or `FAIL` to see the results (if your checks print those words).

If a command prints 10,000 characters or more, though, the agent and the transcript only see
the first and last 5,000 (this is set in the agent's model config). The agent is warned that
the middle was cut, but the PASS or FAIL lines in that middle part are gone unless it reruns the
check more selectively. To avoid this:

- print one short line per rule, for example `PASS 2.1 new workspace returns #general`
- end with a summary line, for example `RESULT: 22 passed, 0 failed`
- do not print full response bodies unless a rule fails

</details>

## 7. Example folder trees

### Component A: roles and invitations

Single-file:

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  roles/
    roles.md
    checks/check_roles.sh
```

Staged:

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  roles/
    roles-1.md
    roles-2.md
    ...
    roles-n.md
    checks/check_roles_1.sh
    checks/check_roles_2.sh
    ...
    checks/check_roles_n.sh
```

### Component B: channels and message history

Single-file:

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  channels/
    channels.md
    checks/check_channels.sh
```

Staged:

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  channels/
    channels-1.md
    channels-2.md
    ...
    channels-n.md
    checks/check_channels_1.sh
    checks/check_channels_2.sh
    ...
    checks/check_channels_n.sh
```

### Both components (Approaches 1 and 3)

Approaches 1 and 3 give the agent both components in one run. Put both folders in `/spec` in the
same layout, and have one `README.md` that names all of them.

Approach 1 (Single-file):

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  roles/                       # component A
    roles.md
    checks/check_roles.sh
  channels/                    # component B
    channels.md
    checks/check_channels.sh
```

Approach 3 (Staged):

```text
/spec/
  README.md                    # the agent reads this first; it names every file below
  roles/                       # component A
    roles-1.md
    roles-2.md
    ...
    roles-n.md
    checks/check_roles_1.sh
    checks/check_roles_2.sh
    ...
    checks/check_roles_n.sh
  channels/                    # component B
    channels-1.md
    channels-2.md
    ...
    channels-n.md
    checks/check_channels_1.sh
    checks/check_channels_2.sh
    ...
    checks/check_channels_n.sh
```

File and folder names are up to you, as long as `/spec/README.md` (at the top of the spec folder)
names them.
