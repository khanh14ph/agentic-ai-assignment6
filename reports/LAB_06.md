# Assignment 6 · Split it, measure it, decide

Model: Google, `gemini-3.5-flash-lite` through the OpenAI-compatible endpoint, the same for every run.

## Part A · The gates, before you build

**The roles I considered.** Five, split by what each touches:

- **Coordinator**: receives the task, starts the others, accepts or rejects what they
  return, reads the end state. It can be code: every decision it makes is a rule.
- **Investigator**: reads the issue, searches and reads the code, decides the label
  and the file. Tools: `issues_read`, `code_search`, `code_read_file`, `code_run_tests`.
- **Writer**: applies the label, opens the draft PR, posts the comment. Tools:
  `issues_label`, `pulls_open`, `issues_comment`.
- **Reviewer** (considered, rejected): checks the investigator's label and file before
  anything is written.
- **Tester / patch author** (considered, rejected): writes a failing case and a patch.
  The daily task never writes a patch (the draft PR names the file), so this pair
  only appears if the task grows; it is slide 38's deadlock shape.

**What each would need from the others mid-task.**

| Two roles | What one needs from the other while the task runs | How it would get it | What that costs |
|---|---|---|---|
| Investigator → writer | The label, the file, and a sentence on what to change there. Nothing the other way: the writer has nothing the investigator needs | Through the lead, between rounds: the investigator's return, checked, becomes the writer's brief | Whatever the return leaves out is lost: the lines it read, the files it ruled out, why. The PR body is only as good as the `change` field |
| Coordinator ↔ investigator | The coordinator needs the finding and whether it is complete (`status`, `could_not_determine`); the investigator needs only its issue number | A structured return at the end of the investigator's loop; a rejection message back if it breaks the contract | A rejected return costs another turn in the investigator's context |
| Coordinator ↔ writer | The writer needs the accepted finding; the coordinator needs to know the writes landed | The brief, before the writer starts; the writes are read from the tracker, never from the writer's words | Nothing mid-task. The check of the end state costs one tracker read per issue |
| Investigator *i* ↔ investigator *j* (batch) | Nothing: the five issues are unrelated, and the repository is read-only | None needed | None. What is lost is that nobody sees across issues (two issues about one file, a duplicate) |
| Writer *i* ↔ writer *j* (batch) | Nothing: each writes on its own issue, from its own branch `repomind/issue-<n>` | None; the tracker is shared, but no two writers touch one issue | A conflict only if two writers were ever given one issue: that is what compare-and-set on the shared record is for |
| Reviewer ↔ investigator (rejected) | The reviewer needs the investigator's whole record of work (what it read and why), or it searches again | Full records in every window, or a second search | Either the window fills with the investigator's history, or the work is done twice. On the same model the reviewer shares the author's blind spots, so its agreement is weak evidence (slides 11, 41) |
| Patch author ↔ tester (rejected) | Each needs the other's output: the patch needs the failing case, the case needs the patch | Messages as they work | Slide 38: each right by its own brief, neither moves |

- **For one issue.** There is one seam, investigator → writer, crossed once, and it is
  a strict sequence: the writer can do nothing until the finding exists. The
  investigation itself cannot be split further: each search depends on what the last
  read showed, so a searcher and a reader would have to talk on every step.
  - Investigator returns: label, file, evidence, the change, status, what it searched
    and did not, calls used, `could_not_determine`. **It cannot say**: why it ruled out
    the files it did not name, the lines it based the change on beyond one sentence,
    how sure it is beyond `found` / `candidate`, or what one more search would have shown.
  - Writer returns nothing the coordinator acts on; its result is in the tracker.
    **It cannot say** whether its PR body is right: it never saw the code.
- **For a batch of five.** The same seam five times, and no arrow between issues. Each
  investigator returns the same contract for its own issue; the coordinator holds five
  findings and never compares them. **Nobody can say** anything across issues (that two
  reports are one bug, or that one issue's file explains another): one agent holding
  all five could notice that, and the split cannot.

**The sketch.** An arrow is something one role needs from another while the task runs.

```
                      runner: task = 1 issue, or a batch of 5; budget B per issue
                                            │
                                  ┌─────────▼──────────┐
                                  │    coordinator     │  code, no model; WaitGraph,
                                  │  (validates every  │  findings board (versioned),
                                  │   return; reads    │  combined check from the tracker
                                  │   the end state)   │
                                  └──┬──────┬──────┬───┘
                 brief: issue #n     │      │      │        (batch: all five at once)
                         ┌───────────▼┐ ┌───▼────┐ ┌▼───────────┐
   reads only ─────────► │ inv-4410   │ │inv-4412│ │ inv-…      │   issues_read, code_search,
                         └─────┬──────┘ └───┬────┘ └─────┬──────┘   code_read_file, code_run_tests
          return (contract) ───┤            │            │          + return tool (local)
                               ▼            ▼            ▼
                         coordinator: schema check, then rules (file exists, was read,
                         label/file agree) ─► rejected: back to the same investigator
                               │ accepted: board[finding/n] = label, file, change (CAS)
                         ┌─────▼──────┐ ┌───▼──────┐ ┌───▼────────┐
   writes only ────────► │ writer-4410│ │writer-…  │ │ writer-…   │   issues_label, pulls_open,
                         └─────┬──────┘ └───┬──────┘ └───┬────────┘   issues_comment (disclosure in code)
                               ▼            ▼            ▼
                         tracker (the run's): label, draft PR, comment per issue
                               │
                               └──► coordinator: combined check of the end state

   No arrow inv-i ↔ inv-j, writer-i ↔ writer-j, or writer → investigator: nothing is
   needed across issues, and nothing flows backwards within one.
```

**The four gates** (slide 46), in order, stopping at the first no.

| Gate | One issue | Why | A batch of five | Why |
|---|---|---|---|---|
| 1. The parts work mostly without talking | pass | For the investigator/writer split there is one handoff and no talk mid-task. (For the triager/patch author/reviewer triple it would fail: all three need the same finding as it forms.) | pass | Five unrelated issues share nothing but the read-only repository and a tracker they write on different issues. Within an issue, one handoff |
| 2. Each part's result can be checked on its own | pass | The finding: the file exists, was read by that worker, label and file agree, all by code. The writes: from the tracker, against the accepted finding | pass | Each issue has its own check (one comment, its label, a PR naming its file or none), and the combined check reads all five from the tracker |
| 3. A team buys what one agent cannot get more cheaply | fail | Nothing runs at once (the writer waits for the finding). One issue's context is a few tens of thousands of tokens, nowhere near the window. Tool-surface specialisation is real, but one agent already gets "label, PR, comment last" from the hook and a planned way more cheaply | pass | Parallelism: five investigations can run at once, which one agent cannot do. Room is the weaker claim: one agent can give each issue a fresh context more cheaply (slide 12), which is exactly arm S-plus | 
| 4. The result is worth the multiplier | not_reached | | pass | On paper, conditionally. At this model's price a batch costs cents even at the lecture's ~15×/4× ≈ 3.75× over one agent, and a wrong comment read by a stranger costs more than that. It is worth it only if the split raises the batch's passes or cuts its wall-clock at a spend one agent cannot match: Part C tests exactly that |

- **The benefit I am trying to buy, and the condition it needs.** For the batch,
  **parallelism** (five investigations at once), which needs issues that are truly
  independent: no shared file, no shared state, no need to compare across issues. Its
  side effect is **context isolation** (each investigator sees one issue), which needs
  little communication (one structured handoff per issue) and is the benefit one agent
  can get more cheaply, so arm S-plus is there to test whether that, not the team, is
  what helps. Specialisation **by tool surface** (the investigator cannot write, the
  writer cannot search) comes with the split and changes what each can do wrong, but
  I do not count it as a reason to split: the hook already holds the writes.
- **My prediction**, as committed in `reports/PREDICTIONS_06.md` (commit `7138d64`,
  pushed before any run of the split): single tasks, M about 1.0× S's tokens and the
  same passes (within one of 18), S-plus as good as M; batches, M about 0.4× S's tokens
  (it spends less, because arm S's one context grows over five issues), M a little
  ahead in passes (S 0 to 1 of 6, M 1 to 2 of 6, about 15 points per issue), and S-plus
  about as good as M, because the win is context isolation that one agent can buy.

## Part B · The split

All of it is on `lab-06-split` (tag `lab-06-split-final`): `ways/split.py` (the
coordinator), `split/roles.py` (the two roles' loops and tool surfaces),
`split/contract.schema.json`, `split/guards.py`, and the prompts
`prompts/investigator.md` and `prompts/writer.md`.

- **The coordinator and the roles, and the tools each holds.**
  - *Coordinator* (`ways/split.py`): code, no model calls, agent `coordinator` with
    parent none. It receives the task from the runner, starts one investigator per
    issue (a batch's five at once, in threads), validates every return, puts each
    accepted finding on a versioned board, starts one writer per finding, and after
    the last write reads the end state from the tracker and checks it. It holds no
    tools of its own: it never reads the code and never writes.
  - *Investigator* (`investigator-<n>`, one per issue): `issues_read`, `code_search`,
    `code_read_file`, `code_run_tests`, and a local `return_finding` tool. It ends when
    a return is accepted.
  - *Writer* (`writer-<n>`, one per accepted finding): `issues_label`, `pulls_open`
    (offered only when the file is not none) and `issues_comment`. It ends when the
    comment is posted.
- **Why they split by tool surface.** The seam in RepoMind's task is between reading
  and writing: everything before the label is reading, everything after is writing,
  and only one thing crosses it (the label, the file and what to change). Splitting
  there gives each role a surface that changes what it can do wrong: the investigator
  cannot post a premature comment, push a branch, or try a merge (in a dev run the
  single agent, holding all ten tools, pushed a commit, ran the tests and tried a
  prohibited merge before it commented on its twelfth and last step; in the measured
  runs this was rare, two pushes in 18 single runs and none on batches); the writer
  cannot re-investigate and drift from the finding. A split by persona on one surface
  (triager, patch author, reviewer) would put three agents on the same reads, needing
  the same finding as it forms, and a same-model reviewer's agreement is weak
  evidence (slides 11, 41). Every call any role makes still goes through the run's
  registry and the Assignment 5 hook; a name outside a role's surface is refused in
  code before the registry (`Surface` in `split/roles.py`).
- **The return contract, field by field** (`split/contract.schema.json`, validated
  with `harness.schema.errors` on every return, `additionalProperties: false` at
  both levels):

  | Field | Type | From | Why the coordinator needs it |
  |---|---|---|---|
  | `result.issue` | integer | the model | a finding for the wrong issue is caught |
  | `result.label` | enum of the four | the model | the label to apply |
  | `result.file` | string, or `none` | the model | the file the PR and comment name |
  | `result.evidence` | string | the model | the comment's explanation |
  | `result.change` | string | the model | the PR body's "what to change" |
  | `status` | `found` / `candidate` / `out_of_budget` | the model | tells "settled" from "best guess" from "stopped": the writer says so in the comment when it is not `found` |
  | `searched` | list of strings | **code**: the `query` of every search call and the `path` of every read call, from the worker's own `tool_call` lines (`Facts` in `split/roles.py`) | what was looked at, as a fact |
  | `not_searched` | list of strings | the model | leads it knows it did not follow |
  | `calls_used` | integer | **code**: the worker's `tool_call` lines for the course's tools | the spend in calls, as a fact |
  | `could_not_determine` | list of strings | the model | what is unsettled, carried into the comment |

  The return tool the model sees has only the model's fields; the coordinator adds
  `searched` and `calls_used` from the worker's calls, overwriting anything the model
  sent. A return that breaks the schema is recorded (`worker_return`, `valid: false`,
  with the errors), never acted on, never repaired, and sent back to the worker with
  the errors. A return that fits the schema then meets the coordinator's rules, set
  before the run (`finding_rules`): it is for the worker's issue; a question names
  none and any other label names a file; a named file exists and **that worker read
  it** (a fact from its calls). Breaking a rule is recorded on the same line
  (`accepted: false`, `problems`) and sent back the same way. Three rejections, or the
  end of the worker's share, and the issue is left unwritten and recorded
  (`unresolved`); the combined check then fails it, visibly.
- **Each worker's share of the budget, and what is held back.** The run's budget is
  `ctx.budget` (B per issue). Each issue gets an equal part, B. Its investigator gets
  75% of that part, its writer 20%, and 5% is held back, so the shares add up to less
  than the run's budget and no worker can spend another's (`shares` line at the top of
  every trace). Each loop checks, in code before every call, its own client's tokens
  plus the next call's estimated cost (the last call's, plus 15%) against its share,
  and the run's total against `ctx.budget`; the harness's hard ceiling is the
  backstop. An investigator holds its last two turns in reserve: when its steps,
  share or wall-clock run low it is told to return, and only the return tool is
  answered from then on. Steps: investigator 10, writer 6; wall-clock 180 s and 120 s.
- **The five checks, where each is in the code.**
  1. *A return contract the code checks*: `split/contract.schema.json`; validated in
     `investigate()` (`split/roles.py`) on every return; `worker_return` line with
     `valid`, `errors`, `accepted`, `problems`; rejected returns go back, never acted on.
  2. *Deadlock detection*: `WaitGraph` (`split/guards.py`). The coordinator writes a
     `wait` line and adds an edge for every wait (on an investigator's finding, on a
     writer's writes) and releases it when the wait ends; a wait that closes a cycle
     returns the cycle, `victim()` names a worker (never the coordinator), and the
     coordinator writes `worker_stopped` and sets that worker's stop flag, which its
     loop checks before every call. Replayed on slide 38's run, it names
     patch-worker → test-worker at turn 1 and stops test-worker.
  3. *A check of the combined result*: `check_combined` (`split/guards.py`), run by the
     coordinator after the last write on `harness.tasks.end_state(...)`, read from the
     tracker; `combined_check` line; a failure ends the run `escalated`. Rules, per
     issue, with no answer key: one comment with label and file lines; label one of
     four, question ⇔ file none; the file exists; the disclosure line; exactly the
     comment's label on the issue; one open PR naming the comment's file, or none.
  4. *Idempotency and compare-and-set on shared writes*: the registry's keys (since
     Assignment 3) plus `SharedState` (`split/guards.py`): the coordinator writes each
     accepted finding to `finding/<n>` by CAS; each writer is briefed with the version
     it read, and every write is refused if the finding has moved since; each write
     first claims its slot (`apply_label/<n>`, `open_pr/<n>`, `comment/<n>`) by CAS from
     the version it read, so a second writer, or two comment calls in one turn, is
     refused instead of landing twice (`GuardedWrites`, `shared_write` lines).
     Replayed on Activity 4, Worker B's stale write is refused and A's fix stays.
  5. *One trace for all agents*: every loop writes through `ctx.agent(...)`'s trace and
     client, so every `model_call` and `tool_call` line names its agent and its parent
     (the coordinator's parent is none), with its waits (`wait`) and its tokens (its
     own client's); calls refused in code are `refused_call` events, not tool calls.

## Part C · Measured

**The five constants** (slide 59), identical in every one of the 72 runs' `run_start`
lines and in `reports/LAB_06.json`:

| Constant | Value |
|---|---|
| Model | `gemini-3.5-flash-lite` (OpenAI-compatible endpoint) |
| Tools | `schemas_sha` `703c0002892f27ec`: the registry's ten definitions, the same in every arm (each role is offered a subset of them; the return tool is local) |
| Budget | B = 250,000 tokens per issue (`agent/bounds.yaml`), so a ceiling of 250,000 per single task and 1,250,000 per batch, the same for S and M; S-plus's budget is arm M's mean spend per task, under M's ceiling |
| Evaluator | `checks_sha` `55abcd67e6a3b208` (`eval/check_task.py`, `eval/check_triage.py`, the answers), reviewer `scripted:approve-all` |
| Task set | `batch-a`, `batch-b`, `one-4402`, `one-4403`, `one-4405`, `one-4406`, `one-4409`, `one-4413` |

Every arm ran with `--jobs 4`, one arm after another, three sweeps each. No run was
rate-limited or stopped by the quota, so no waits are hidden in the wall-clock.

**The results** (`results/runs_06.jsonl`, written by `eval/results_06.py` from
`eval/check_task.py` and the `run_end` lines; recomputed by `make check-06`).

| Arm | Kind | Passes / runs | Lowest and highest sweep | Tasks all three passed | Mean tokens | Mean wall-clock (s) |
|---|---|---|---|---|---|---|
| S | single | 11 / 18 | 3 to 4 | 3 of 6 | 51,572 | 7.2 |
| S | batch | 4 / 6 | 1 to 2 | 1 of 2 | 356,827 | 28.6 |
| M | single | 16 / 18 | 5 to 6 | 5 of 6 | 26,115 | 7.2 |
| M | batch | 3 / 6 | 1 to 1 | 1 of 2 | 137,694 | 9.8 |
| S-plus | single | 15 / 18 | 5 to 5 | 4 of 6 | 28,870 | 7.9 |
| S-plus | batch | 4 / 6 | 1 to 2 | 1 of 2 | 139,438 | 34.9 |

Per task (passes of three, and mean tokens):

| Task | S | M | S-plus |
|---|---|---|---|
| one-4402 | 0 · 75,082 | 1 · 46,051 | 1 · 46,048 |
| one-4403 | 3 · 32,839 | 3 · 21,683 | 3 · 19,881 |
| one-4405 | 3 · 38,993 | 3 · 20,913 | 2 · 29,581 |
| one-4406 | 0 · 70,015 | 3 · 27,051 | 3 · 37,223 |
| one-4409 | 2 · 56,442 | 3 · 23,023 | 3 · 22,758 |
| one-4413 | 3 · 36,063 | 3 · 17,969 | 3 · 17,725 |
| batch-a | 1 · 374,096 | 3 · 119,070 | 3 · 129,255 |
| batch-b | 3 · 339,558 | 0 · 156,317 | 1 · 149,622 |

Per issue on the batches (5 issues × 6 runs): S 27/30, M 25/30, S-plus 27/30. A batch
passes only if all five do, so one hard issue decides it: batch-b's 4431 (a feature
request that reads like a bug) failed in all three M runs and two S-plus runs.

- **My multiplier (M over S): 0.51 on single tasks, 0.39 on batches.** The split
  spent *half* of what one agent spent, and less than half on a batch, where the
  lecture's figure is about 15× a chat for a multi-agent system against about 4× for
  one agent, so roughly 3.75× one agent. The lecture's numbers are Anthropic's,
  typical use on its own workload, measured against a chat and averaged; mine is one
  design against one agent, on one task set of eight, with a cheap model. The reason
  mine runs the other way is mechanical: arm S sends all ten tool definitions (about
  3,700 tokens) on every call and carries its whole history, including on a batch all
  five issues in one context; arm M's investigator carries four definitions and the
  return tool, its writer two or three, each with one issue's short history, and its
  coordinator is code and spends nothing. Most of the multiplier is about the window,
  not about the number of agents.
- **Whether S-plus's spend matched M's in fact, task by task.** On the means, yes:
  S-plus spent 1.11× M on single tasks (28,870 against 26,115) and 1.01× on batches
  (139,438 against 137,694). Task by task it ranged from 0.92× (one-4403) to 1.41×
  (one-4405) and 1.38× (one-4406): S-plus's first pass is bounded like M's two
  workers, not by the matched budget, and on those two tasks its first attempts ran
  longer than M's investigators did. The matched budget bought a second attempt on
  only 3 of 48 issues (one-4402 once, two batch-b issues), and no vote was ever
  contested: at arm M's spend, one agent can afford almost exactly one investigation
  and one write per issue, the same work M does, done in sequence.
- **Arm S-plus was measured three times; two measurements are discarded.** Both were
  defects in my rival's bookkeeping, seen in its spend and its traces, not in its
  answers, and both are kept: `results/splus_first_measurement_discarded.json`,
  `results/splus_second_measurement_discarded.json`, traces in `runs/discarded/`. The
  first capped each attempt at the issue's part minus a write reserve while the loop
  held back two turns' cost, so it told attempts to return before they had read a
  file: 6/18 single and 1/6 batch, spending 48% to 78% of its single budgets. The second
  capped each batch issue's first attempt and its write at an even fifth of the
  budget, which M's workers never were, and cut writes before their comment after the
  vote had chosen the right answer: 16/18 single, 1/6 batch. The third, reported above,
  gives the first attempt and the write exactly M's worker shares and spends the
  matched budget on further attempts. Arm M's code path did not change (a new
  parameter whose default is M's behaviour). This is a forking path, and I name it: had I
  stopped at the first version, I would have reported that one agent at the same
  budget does far worse, and that would have been an artefact of my code.

**Slide 18's three questions**, for each kind of task.

| Question | One issue | A batch of five |
|---|---|---|
| Did the split spend more? | **No.** 26,115 against 51,572 tokens per task: M spent 0.51× S | **No.** 137,694 against 356,827: 0.39× |
| Does spending more raise the score? | **No.** One agent at two budgets: arm S at 51,572 passed 11/18, arm S-plus at 28,870 passed 15/18. More spend went with a lower score | **No.** Arm S at 356,827 passed 4/6 (27/30 issues); arm S-plus at 139,438 passed 4/6 (27/30). The same score at 2.6× the spend |
| Given the same budget, did one agent do as well? | **Yes.** S-plus 15/18 at 1.11× M's spend against M's 16/18: within one pass, and inside the sweep spread (S-plus 5 to 5, M 5 to 6) | **Yes, and better.** S-plus 4/6 (27/30 issues) at 1.01× M's spend against M's 3/6 (25/30) |

The second row's two one-agent arms are not one design at two budgets: arm S is one
loop holding every tool, whose "post by your tenth step" lives only in its prompt;
arm S-plus investigates in fresh, read-only contexts with a return turn enforced in
code, then writes. So "spending more does not raise the score" here really says that
arm S's extra tokens buy nothing that the cheaper design does not already get, which
is the same conclusion slide 18 points to, reached on my own system.

- **What I would have claimed from arms S and M alone**, and what I claim now. From S
  and M alone: on single issues the split passes 16/18 against 11/18 at half the
  tokens, so splitting by tool surface makes RepoMind both better and cheaper; on
  batches it is 2.6× cheaper and 2.9× faster at about the same accuracy. Slide 18's
  first question already answers "no", so the extra spend cannot explain the
  single-issue win, and slide 18's reading would credit the *design*. But the third
  question says one agent at the same budget does as well, so the credit goes to
  parts of the design that one agent can have, not to there being two agents:
  - a return turn **enforced in code** (arm S's single failures are all `turn_limit`
    runs that searched past step 12 without posting: one-4402 three times, one-4406
    three times, one-4409 once; M's and S-plus's investigators are told to answer at
    their closing turn, and post);
  - a **fresh, small context** per issue with only the tools the phase needs, which is
    where the 2× to 2.6× saving comes from;
  - the **checked return** (schema, then rules) and the **guarded writes**, which S-plus
    uses too. No return in arm M ever broke the schema; the rules sent back 8, and 6 of
    them were a question that named a file (4412, 4426, 4403), each fixed on the retry
    and then passed. Arm S, without that rule, labelled 4412 a bug with a file in two of
    its three batch-a runs: that one rule is most of batch-a's 3/3 against 1/3. The two
    others were the `"null"`-filter returns below, which had nothing to fix them with.
  What I claim now: the split's only measured advantage that one agent did not match
  is **wall-clock on a batch**, 9.8 s against S-plus's 34.9 s and S's 28.6 s, from five
  investigations at once. On single issues the split is no faster (7.2 s, the same as
  S) because its two roles run one after the other.
- **Against my prediction** (`reports/PREDICTIONS_06.md`, commit `7138d64`). I
  predicted M ≈ 1.0× S on single tasks with equal passes; it was 0.51× and five passes
  better, because I underestimated how much arm S pays for its ten tool definitions
  and how often it runs out of steps without posting. I predicted M ≈ 0.4× S on
  batches (it was 0.39×) but for the wrong reason: I expected arm S's one context to
  hit the ceiling or the step limit, and it never did (its largest batch was 390,660
  of 1,250,000); its 60 pooled steps let a hard issue borrow from easy ones, which
  helped it, and it passed 4/6, not the 0 to 1 I predicted. I predicted S-plus would
  do as well as M on both kinds; it did, and on batches better.

**Every arm M run that failed.** Five of 24. None involved state the agents shared
going wrong: in every one the coordinator's waits released in order (no cycle, no
`worker_stopped`), every `shared_write` succeeded on its first try, and no writer was
refused a write. In each, one investigator got its own part wrong and the others
acted on it faithfully, or nothing acted at all.

| Run | What each agent read, wrote and waited on | The first thing that went wrong | Anomaly | MAST category |
|---|---|---|---|---|
| `split-batch-b-s01-973ed6` | Five investigators read the issue tracker and the repository (read-only; nothing shared); the coordinator waited on each, then on three writers; the writers wrote only their own issue | `investigator-4424` and `investigator-4429` sent `path_glob` as the string `"null"` on all seven of their searches, so every search matched nothing. Each reached its closing turn, returned `bug`, file `none`, `out_of_budget`; the coordinator's rule (a bug names a file) rejected it, the retry turn went on a refused search, and nothing was written for either issue. The combined check failed both, and the run ended `escalated`. Separately, `investigator-4431` read both `src/exportUtils.ts` and `src/util/strings.ts` and returned the first as `found` | none | system_design: a step repeated seven times on a broken argument, with no detector for a worker making no progress, and a stopping rule that leaves an issue unwritten. The string `"null"` filter appears only in arm M (28 of 139 searches; 0 of 119 in arm S): the same tools offered as a smaller set beside a strict return tool changed how the model fills an optional argument |
| `split-batch-b-s02-908a6d` | As above; all five findings accepted, five writers | `investigator-4431` returned `bug` with status `found` for a feature request (non-Latin report names stripped from export file names), with the right file; the writer wrote it as briefed | none | task_verification: the label was never verified; my rules check its form, not which of bug or feature is right, and a same-model reviewer would be weak evidence (slide 41) |
| `split-batch-b-s03-87bda3` | As above | The same: `investigator-4431` returned `bug`, `found`, for the feature request | none | task_verification, as above |
| `split-one-4402-s01-8c63c9` | One investigator: six searches (a seventh refused at its closing turn), one read (`src/ingest/parser.ts`); the coordinator waited on it, then on the writer | Told to return at its closing turn, the investigator returned `src/ingest/parser.ts` as a `candidate`. The coordinator's rules accept a candidate that exists and was read, so the writer wrote it (saying it was not certain) | none | task_verification: the run acted on an unverified candidate; the closing turn ended the search early (premature termination) |
| `split-one-4402-s03-3b330e` | One investigator: five searches, two reads (`parser.ts`, `src/ingest/errors.ts`) | The same: `parser.ts` returned as a `candidate` at the closing turn, accepted, written | none | task_verification, as above |

The same issues failed in arm S-plus (4431's label twice, 4402's `parser.ts` twice, two
issues unanswered), which shares M's investigation: the topology did not change what
the investigation gets wrong. Arm S failed differently, by running out of steps on
single issues, and, on batch-a, by labelling the question 4412 a bug twice and choosing
the wrong file for 4414 once.

## Part D · The decision

- **One issue: one agent.** The split was cheaper and better than arm S, but one agent
  given the split's actual spend did as well (15/18 against 16/18), and on one issue
  the split is no faster. What helped (the return turn in code, a small fresh context,
  the checked answer, the guarded writes) can all live in one agent.
- **A batch of five: one agent**, which gives each issue a fresh context in turn, as
  arm S-plus does. At the split's spend it passed more (4/6 and 27/30 against 3/6 and
  25/30). The split's one advantage is wall-clock: 9.8 s against 34.9 s. Nothing in
  RepoMind's daily task needs the batch done in ten seconds rather than forty: it runs
  once a day and nobody waits on it.
- **The condition that would reopen it.** For the batch: a split of the investigation
  passes at least as many issues as one agent given the split's actual spend (arm
  S-plus, `--match split`), on a fresh batch task set (at least two batches of five
  unrelated issues, each with its own known answers, three sweeps, B = 250,000 per
  issue, the same model and checks), **and** the batch acquires a wall-clock
  requirement that one agent's sequential run misses and the split meets (here 34.9 s
  against 9.8 s). My batch result speaks to slide 49's condition (a batch of separate
  issues, each with its own check, run at once): run at once, it was 3.6× faster, and it
  did not pass more. For a single issue: a split that beats arm S-plus at the same
  spend by more than its sweep-to-sweep spread.
- **The record:** `decisions/0006-topology.md`. RepoMind stays one agent in this
  repository; `lab-06-split` is not merged.

## AI assistance

Claude Code (Claude Opus 5.5) wrote, at my request: the split on `lab-06-split`
(`ways/split.py`, `split/roles.py`, `split/guards.py`, `split/contract.schema.json`,
the role prompts), `ways/splus.py` and `eval/results_06.py`; it ran the free scripted
tests, the dev runs and the 72 measured runs, and drafted this report, the prediction
and the decision record. What was checked: `python checks/check_06.py --here` on the
branch (every guard replay passes; the contract and one-trace rows were also run on
the scripted and dev runs before measuring); every number in Part C was recomputed by
`eval/results_06.py` from `eval/check_task.py` and the `run_end` lines and agrees with
`make check-06`; every arm M failure was read from its trace, not from any agent's
report; the two discarded S-plus measurements were diagnosed from their traces and
spend and are kept in `results/` and `runs/discarded/`.
