## Milestone 1

The goal of this milestone is to create specifications (specs) for two components of a Slack
clone.

The agent is given `instruction.md`, which describes the whole Slack clone. On its own, that is
not enough. It covers far more than one 50 minute run can build, it does not say what to build
first, and it gives the agent no way to test whether its code works. Without a spec and its associated checks, the agent
builds a little of everything, tests nothing, and submits.

Your spec fixes this. It tells the agent what to build: the `instruction.md` lines for the
component, each written as a rule a script can check. A rule like "handle duplicate workspaces"
is too vague to test. A rule like "creating a workspace with a slug that is already taken returns
`409`" names an exact request and an exact result, so a check can test it. Your spec also says which rules to build first, how to test
them with the checks you write, and when to submit, for example once every check passes.

The two components are:

- **Component A: roles and invitations**, including auth, workspaces, joining channels, workspace
  roles, role changes, ownership transfer, invitations, and join mode
- **Component B: channels and message history**, including channel management, history and
  paging, mentions, read state, and event ordering

For each component you write two things:

- **A spec:** rules that say exactly what the code must do.
- **Checks:** small scripts that test the running app against each rule and print PASS or FAIL.
  The agent runs them while it works to find out whether its code is correct.

You will write each spec in two layouts, single-file and staged, and compare how well the agent
does with each one.

The instructions for this milestone are split across three files:

- This **README**
- **[`GUIDE.md`](GUIDE.md):** how to write a spec and its checks, with an example spec
- **[`COMPONENTS.md`](COMPONENTS.md):** the exact `instruction.md` lines components A and B must cover

## 1. Layouts and approaches

You write each component's spec in two layouts:

- **Single-file:** one spec file and one check per component
- **Staged:** n chained spec files per component (A.1 to A.n, B.1 to B.n), each with a check

See [layouts](GUIDE.md#5-the-two-layouts) for details.

There are four approaches:

- **Approach 1:** Single-file layout, A and B together from the final specs:
  - Spec A and Spec B are combined and given to the agent at the same time.
  - Checks A and B are also included.
  - There are no internal splits within either component's spec.
  - The agent builds A and B in the same run and produces a single `/app` containing both.
- **Approach 2:** Single-file layout, one component at a time:
  - **Component A:** run the agent with Spec A and its check, review the run (see
    [section 4](#4-reading-a-run)), revise Spec A, and run again, within your budget. When you
    are happy with Spec A, freeze it.
  - **Component B:** with Spec A frozen, proceed to Spec B and its check, and repeat the same
    steps.
- **Approach 3:** Staged layout, A and B together from the final specs:
  - Sections A.1 to A.n and B.1 to B.n are given to the agent at the same time.
  - Checks A.1 to A.n and B.1 to B.n are also included.
  - The agent works through the sections in order.
  - The agent builds A and B in the same run and produces a single `/app` containing both.
- **Approach 4:** Staged layout, one component at a time:
  - **Component A:** run the agent with sections A.1 to A.n and checks A.1 to A.n, review the
    run (see [section 4](#4-reading-a-run)), revise Spec A, and run again, within your budget. When
    you are happy with Spec A, freeze it.
  - **Component B:** with Spec A frozen, proceed to sections B.1 to B.n and checks B.1 to B.n,
    and repeat the same steps.

You will try all four approaches. Staff will replay Approaches 1 and 3 with your final specs for
grading (see [Grading](#6-grading)).

> [!TIP]
> We recommend using Approaches 2 and 4 to build and debug your specs. Working on one component
> at a time makes it easier to get Spec A and Spec B right before running them together in
> Approaches 1 and 3. This is a suggestion, not a requirement, and the numbering of the approaches does not set
> the order you should try them in.

## 2. Set up (once)

> [!NOTE]
> You should already have done steps 1 to 3 (Docker, `uv` and your key) in milestone 0. They are
> here just in case. **Step 4 is new for milestone 1.**

1. **Docker**, usable without `sudo`. Install it from the
   [official guide](https://docs.docker.com/get-docker/) (Docker Desktop on macOS and Windows; it
   must be running whenever you run the agent). On Linux, also run
   `sudo usermod -aG docker $USER` and log back in. Check it with:

   ```bash
   docker run hello-world
   ```

2. **`uv`**, the Python package manager the scripts use:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv --version
   ```

3. **Your key.** Put the key the TAs gave you in `dev.env` at the repo root. For OpenAI (the
   default), from the repo root (on Linux, drop the `''` after `-i`):

   ```bash
   sed -i '' 's|^OPENAI_API_KEY=.*|OPENAI_API_KEY="<your-key>"|' dev.env
   ```

   <details>
   <summary>Knowing which script to use</summary>

   - **OpenAI key (default):** fill in `OPENAI_API_KEY` and use `run_student_openai.sh`.
   - **OpenRouter key:** fill in `OPENROUTER_API_KEY` and use `run_student_openrouter.sh`.
   - **AWS Bedrock credentials:** fill in `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
     `AWS_REGION` and `BEDROCK_ARN_GPT_5_6_LUNA`, and use `run_student_bedrock.sh`.

   Use the same script for every run.

   </details>

> [!WARNING]
> **Monitor your usage. No run should cost more than about $3.** The cost is printed at the end
> of every run (`COST: total=$...`). If you run out of OpenAI credits, inform the staff
> immediately.
>
> **Your credits are limited, so you cannot rerun as many times as you like.** Make each run
> count: before running again, fix every problem you found in the last run.

4. **Link the spec folder.** The run script refuses to start without this link:

   ```bash
   cd tasks/slack-clone-course/student-files
   ln -sfn ../course-staff-files/final_task/environment/milestone-specs/milestone-1-specs spec
   ```

   `spec` now points at `milestone-1-specs/`, the folder you write your spec in. The run copies
   it into the agent's container as `/spec`, and the agent is told to read `/spec/README.md`
   first.

## 3. Running the agent

Try the approaches in [section 1](#1-layouts-and-approaches) in any order. For every run:

1. Write the spec and checks following [`GUIDE.md`](GUIDE.md), covering the lines in
   [`COMPONENTS.md`](COMPONENTS.md).
2. Put the spec and checks for that run directly in `milestone-1-specs/`
   (`tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-1-specs/`),
   with `README.md` at its top level. The run copies this folder into the agent's container as
   `/spec`, so it holds one layout at a time:
   1. Write the single-file layout directly in `milestone-1-specs/`.
   2. Run. The agent sees it as `/spec/`.
   3. When you are done with that layout, move it out of the folder, write the staged layout in
      `milestone-1-specs/` the same way, and run.
   4. Before submitting, put each finished layout in its own subfolder:
      `milestone-1-specs/single-file/` and `milestone-1-specs/staged/` (see
      [section 5](#5-what-to-submit)).
3. Run the agent from `tasks/slack-clone-course/student-files`:

   ```bash
   ./run_student_openai.sh 1   # or run_student_openrouter.sh / run_student_bedrock.sh
   ```

4. Read the result ([section 4](#4-reading-a-run)). The agent runs your checks during the run,
   so look in the transcript for whether it ran them, what they printed last, and whether it
   submitted with failures still showing.
5. Fix the spec, not the code, before running again. Stay within your budget (see the warning
   in [section 2](#2-set-up-once)).

## 4. Reading a run

A run takes up to 50 minutes and ends with a table and a cost line:

```text
┏━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┓
┃ Trials ┃ Exceptions ┃  Mean ┃
┡━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━┩
│      0 │          0 │ 0.000 │
└────────┴────────────┴───────┘
Results written to .../tasks/slack-clone-course/jobs/<timestamp>/result.json
COST: total=$<amount>
```

- **Stuck on `starting environment...`:** normal. The first Docker build can take up to 20
  minutes; later builds take seconds.
- **`Trials 0`, `Mean 0.000`:** expected. Ignore it.
- **`Exceptions 0`:** the agent ran and exited cleanly.
- **`COST: total=$...`:** what the run cost.
- **The run took a minute or two and cost very little:** the agent quit early. Run it again
  once; if it keeps happening, your spec or setup has a bug.

Results are in `tasks/slack-clone-course/jobs/<timestamp>/<trial>/`:

- **`agent/mini-swe-agent.txt`:** the full transcript. Check that the agent read your README,
  ran your checks, and what they printed last. Search it for your check scripts' file names to
  find each time the agent ran one, and for `PASS` or `FAIL` to see the results (see the tip in
  [GUIDE section 6](GUIDE.md#6-checks)).
- **`artifacts/app/`:** the code the agent wrote.
- **`result.json`:** timings, tokens and cost; `exception_info: null` means no crash.
- **`agent/format_errors.jsonl`:** malformed agent replies. A few are fine.

When a run goes wrong, look for a rule the agent misread, a rule your checks do not cover, or a
submit with failures still showing. Then change the spec.

<!-- AIDEV-TODO: staff to state the allowed number of runs or the budget per student. -->

## 5. What to submit

> [!IMPORTANT]
> **Submit the whole repo.** Make sure it contains:
>
> - **Your final specs and checks, in `milestone-1-specs/`.** Once you are done running, put both
>   layouts there, each in its own subfolder holding exactly what the agent got for that run
>   (README, Spec A, Spec B and all checks):
>   - `milestone-1-specs/single-file/`: your Single-file specs (Approaches 1 and 2)
>   - `milestone-1-specs/staged/`: your Staged specs (Approaches 3 and 4)
>
>   During runs the layout sits loose in `milestone-1-specs/`, so move each one into its subfolder
>   before you submit (step 2 in [section 3](#3-running-the-agent)).
> - **The job folder of every run you did, for every approach:** each
>   `tasks/slack-clone-course/jobs/<timestamp>/` folder. Each one holds the code the agent wrote
>   (`artifacts/app/`, the run's `/app`), the transcript and `result.json`.
> - `report.md`
>
> **What `report.md` must contain.** Write one section per approach (1 to 4). In each section:
>
> - **List every run of that approach.** For each run, record:
>   - the component (A, B, or both)
>   - the version of the spec and checks
>   - the `<timestamp>` of its job folder
>   - your check results (passed and failed counts)
>   - any failures, unexpected behavior, or implementation problems
> - **Give the final `/app` for that approach:** which job folder holds it, and a summary of
>   your check results for A and B (for example, passed and failed counts for each check).
>
> End with a short comparison of the four approaches.

A blank template is in [`report.md`](report.md) at the repo root. You may fill it in or use your
own format, as long as it has all the information above. You do not have access to the hidden
tests (see [Grading](#6-grading)), so your results will be from your own checks.

## 6. Grading

Grading happens in this order:

1. **Required files (pass or fail):** every item in [What to submit](#5-what-to-submit) is
   present: both specs in both layouts, their checks, the job folders of your runs, and
   `report.md`.
2. **`report.md` (10%):** it contains every item listed in [section 5](#5-what-to-submit), for
   each of your runs, plus the comparison.
3. **Hidden tests (90%):** staff run your final specs as Approaches 1 and 3 with GPT 5.6 Luna against the hidden
   tests for components A and B. The hidden tests also still check the four milestone 0 boot
   conditions. The better of the two scores counts.

**Missing files mean a 0.** If any required file is missing, that part scores 0, and staff
cannot run your specs, so the hidden tests (90% of the grade) cannot be scored either.

---

<h1 align="center">SWE-Marathon</h1>

<p align="center"><em>Can agents autonomously complete ultra-long-horizon software work?</em></p>


## Links

- [Website](https://www.swe-marathon.org/)
- [Paper](https://arxiv.org/abs/2606.07682)
- [Leaderboard](https://www.swe-marathon.org/#leaderboard)
- [Tasks](https://www.swe-marathon.org/#tasks)


## News
- [09/2026] 🚀 Featured on the [Qwen3.8-Max model card](https://x.com/Alibaba_Qwen/status/2094968708288680276?s=20)!
- [08/2026] 🔶 Featured on the [Hy4 preview model card](https://hy.tencent.ai/research/hy4-preview)!
- [08/2026] ⭐ Featured on the [GLM 5.3 model card](https://z.ai/blog/glm-5.3)!
- [08/2026] ⚙️ Featured on the [Grok 4.6 model card](https://media.x.ai/v1/website/card-4p6-4cd2dc57.pdf)!
- [07/2026] ➕ SWE-Marathon [v1.1 released](https://x.com/rishi_desai2/status/2087216551653146706?s=20)!
- [07/2026] 🌙 Featured on the [Kimi K3 model card](https://www.kimi.com/blog/kimi-k3)!
- [07/2026] 🔥 Featured on the [Grok 4.5 model card](https://x.ai/news/grok-4-5)!
- [06/2026] 🚀 Featured on the [GLM 5.2 model card](https://z.ai/blog/glm-5.2)!
- [06/2026] ⚡ SWE-Marathon [v1.0 released](https://x.com/rishi_desai2/status/2062930906818769356?s=20)!


## Citation

```bibtex
@misc{swemarathon_2026,
  title        = {{SWE-Marathon: Can Agents Autonomously Complete Ultra-Long-Horizon Software Work?}},
  author       = {Rishi Desai and Jesse Hu and Joan Cabezas and Neel Harsola and Pratyush Shukla and Daniel Wang and Xiangyi Li and Roey Ben Chaim and Adnan El Assadi and Omkaar Mukund Kamath and Fenil Faldu and Prannay Hebbar and Jiankai Sun and Yiyuan Li and Pramod Srinivasan and Ishan Gupta and Christopher Settles and Derek Chen and Pranav Raja and Albert Liu and Marek Šuppa and Nevasini Sasikumar and Luyang Kong and Erik Quintanilla and Ivan Bercovich and Steven Dillmann},
  year         = {2026},
  howpublished = {\url{https://arxiv.org/abs/2606.07682}},
  note         = {arXiv:2606.07682}
}
```

## License

[Apache License 2.0](LICENSE)
