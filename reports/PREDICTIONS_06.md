# Assignment 6 · Predictions, committed before any run of the split

Written on paper, before the split exists and before any run of it, on any task.
Model: gemini-3.5-flash-lite (OpenAI-compatible endpoint), the same for every arm.
B = 250,000 tokens per issue (`agent/bounds.yaml`), so a batch's ceiling is 1,250,000.

## The design these predictions are about

- **Coordinator**: code, no model calls. It fans out one investigator per issue (all
  five at once on a batch), validates each return against the contract, accepts or
  re-asks, then starts one writer per accepted finding, and finally checks the
  combined end state from the tracker.
- **Investigator** (one per issue): `issues_read`, `code_search`, `code_read_file`,
  `code_run_tests`, and a local return tool. It never writes.
- **Writer** (one per issue): `issues_label`, `pulls_open`, `issues_comment`. It never
  reads the code; it writes from the accepted finding only.

## Why I expect what I expect

Arm S sends all ten tool definitions (about 3,700 tokens) on every call, and its
context keeps every issue, search result and file it has read. On one issue that
is about 9 calls; on a batch it is about 45 calls in **one** growing context, so
the fifth issue's calls re-read the first four issues' history. Arm M's
investigator carries five tool definitions and one issue's history, and its writer
carries three definitions and a short brief, so the label, PR and comment calls
happen in a small context instead of the largest one. The split adds a return call
and a writer's own system prompt per issue, which costs something, but on a batch
the cost of the shared context is much larger than the cost of the seams.

## Single tasks (six tasks × three sweeps = 18 runs per arm)

- **Tokens per task, M over S: about 1.0×** (I would not be surprised anywhere in
  0.8× to 1.3×). The investigation is the same work; the split saves the large-context
  write calls and pays for a return call and a writer.
- **Passes: about the same.** I predict S and M within one pass of each other out of
  18 (my guess: S 12/18, M 12/18). Accuracy on one issue is set by whether the search
  finds the right file, and both arms use the same model and the same search. The
  split can lose a little at the handoff (the writer only knows what the return says)
  and gain a little from the code checks on the return (a named file must exist and
  must have been read).
- **S-plus vs M: S-plus does as well as M.** M's spend on one issue should be close
  to S's, so S-plus is roughly S with at most one extra read-only attempt, and a split
  that ties one agent on one issue gives nothing for its seams.

## Batch tasks (two tasks × three sweeps = 6 runs per arm)

- **Tokens per task, M over S: about 0.4×** (0.3× to 0.6×): the split spends *less*,
  because arm S's single context grows across five issues while each investigator
  starts fresh. I expect some arm S batch runs to hit the 1,250,000 ceiling or the 60
  step limit before the fifth comment.
- **Passes: M passes more, but by little in tasks.** A batch passes only if all five
  issues pass, so both arms will pass few batches: my guess is S 0 to 1 of 6, M 1 to 2
  of 6. Per issue (30 issue-runs per arm) I expect M to be about 15 percentage points
  higher than S, from fresh context per issue and from no shared step budget.
- **S-plus vs M: S-plus does about as well as M.** Given M's (smaller) spend, one
  agent that gives each issue a fresh context, attempt by attempt, gets the same
  context isolation without a team (slide 12). So I expect the batch win of M over S
  to be explained by context isolation, which one agent can buy more cheaply, not by
  the split itself; any extra gain of M would come from parallelism (wall-clock), not
  from passes.

## Wall-clock

M's batch runs should take roughly a third to a half of S's wall-clock, because the
five investigators run at once. On single tasks M should be a little slower than S
(the writer is a second, sequential loop).

## In one line

I predict the split ties one agent on single issues at about the same spend, and
beats it on batches mainly by spending less in a fresh context per issue, a benefit
arm S-plus will also get, so I expect the decision to be one agent for single issues
and, at most, a split of the batch's investigation for wall-clock.
