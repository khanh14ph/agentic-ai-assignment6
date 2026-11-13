# 0006 · Topology: RepoMind stays one agent

- **Status:** decided, 2026-11-13. Revisit on the condition below.
- **Decision:** one agent for a single issue; one agent for a batch of five, giving
  each issue a fresh context in turn. No split is merged. The split that was built and
  measured is on the branch `lab-06-split`, tag `lab-06-split-final`, and stays there.
- **Evidence:** `reports/LAB_06.md` (Parts A to D), `reports/LAB_06.json`,
  `results/runs_06.jsonl` (72 runs), `runs/`, `tracker/`.

## What was tested

A split of RepoMind's daily task (read an issue, find the responsible file, apply the
label, open a draft PR when a file is responsible, post the comment last, with the
disclosure line), against the single agent of Assignment 5:

- **Arm S**, the single agent: one loop holding all ten tools (`ways/agent.py`).
- **Arm M**, the split: a coordinator in code; per issue, an investigator holding
  only reads (`issues_read`, `code_search`, `code_read_file`, `code_run_tests`) and a
  checked return contract, then a writer holding only writes (`issues_label`,
  `pulls_open`, `issues_comment`), guarded against the accepted finding; on a batch,
  the five investigations run at once. With the five checks of slide 47: the
  contract, deadlock detection, the combined check from the tracker, compare-and-set
  on shared writes, one trace.
- **Arm S-plus**, one agent given arm M's actual spend on each task: read-only
  attempts in fresh contexts, one after another, a vote, one write.

Eight tasks (six single issues, two batches of five), three sweeps per arm.

**The gates, before building** (Part A). Daily task, one issue: pass, pass, **fail**,
not reached: nothing runs at once, the context is small, and the tool-surface split
one agent gets more cheaply from the hook. Batch of five: pass, pass, pass, pass, the
last conditionally: parallelism is the one benefit one agent cannot have, and the
batch is worth a multiplier only if the split raised its passes or its wall-clock at
a spend one agent could not match. **The prediction** (`reports/PREDICTIONS_06.md`,
commit `7138d64`, pushed before any split run): single tasks, M ≈ 1.0× S's tokens and
the same passes; batches, M ≈ 0.4× S's tokens and a little ahead; S-plus as good as M
on both.

## What was found

**The five constants**, identical in all 72 runs: model `gemini-3.5-flash-lite`;
tools `schemas_sha 703c0002892f27ec`; budget B = 250,000 tokens per issue (ceiling
250,000 per single task, 1,250,000 per batch, the same for S and M; S-plus at M's mean
spend per task); evaluator `checks_sha 55abcd67e6a3b208` with reviewer
`scripted:approve-all`; task set `batch-a`, `batch-b`, `one-4402`, `one-4403`,
`one-4405`, `one-4406`, `one-4409`, `one-4413`.

| Arm | Single: passes, mean tokens, wall-clock | Batch: passes (issues), mean tokens, wall-clock |
|---|---|---|
| S | 11/18, 51,572, 7.2 s | 4/6 (27/30), 356,827, 28.6 s |
| M | 16/18, 26,115, 7.2 s | 3/6 (25/30), 137,694, 9.8 s |
| S-plus | 15/18, 28,870, 7.9 s | 4/6 (27/30), 139,438, 34.9 s |

**Multiplier, M over S: 0.51 (single), 0.39 (batch).** The lecture's figure is about
15× a chat for a multi-agent system against about 4× for one agent (Anthropic's,
typical use on its own workload), so about 3.75× one agent. Here the split spent
less, because arm S sends all ten tool definitions with every call and keeps all its
history in one window, while each of M's workers carries a few definitions and one
issue.

**Slide 18's three answers.**

| | One issue | A batch of five |
|---|---|---|
| Did the split spend more? | no (0.51×) | no (0.39×) |
| Does spending more raise the score? | no: one agent at 51,572 passed 11/18, at 28,870 passed 15/18 | no: one agent at 356,827 and at 139,438 both passed 4/6 (27/30) |
| Given the same budget, did one agent do as well? | yes: 15/18 against 16/18, within the sweep spread | yes, better: 4/6 (27/30) against 3/6 (25/30) |

The split did not win by spending more; where it beat arm S (single issues), one
agent at the same spend did as well. What did the work can all live in one agent: a
return turn enforced in code (arm S's single failures are runs that searched past
step 12 and never posted), a small fresh context per issue, a return checked by
schema and rules before anything is written (the rule "a question names no file"
fixed six returns in arm M), and writes checked against the answer. The split's one
advantage one agent did not match is wall-clock on a batch: 9.8 s against 34.9 s.
Every failed arm M run (5 of 24) was one investigator's own error, acted on
faithfully, never a failure between agents: no wait cycle, no refused shared write, no
misread brief (anomaly `none`; MAST system_design once, task_verification four times).

Arm S-plus was measured three times; the first two versions had bookkeeping defects
that starved it (attempts told to return before reading a file; writes cut short),
were discarded with `--discard`, and are kept in `results/` and `runs/discarded/`.
Had I stopped at the first, this record would say that one agent does far worse at
the same budget.

## The decision

- **One issue: one agent.** The split is not faster on one issue (its roles run in
  sequence), and its accuracy and its spend are matched by one agent with the same
  mechanisms.
- **A batch of five: one agent**, giving each issue a fresh context in turn. At the
  split's spend it passed more issues. The split's gain is wall-clock, and the daily
  batch has no wall-clock requirement: it runs once a day and nobody waits on it.

## The condition that would reopen it

Reopen **the batch**, and only its investigation, when both hold:

1. On a fresh batch task set (at least two batches of five unrelated issues, each with
   its own known answers; three sweeps; B = 250,000 per issue; the same model, tools
   and checks), a split whose investigators run at once passes at least as many issues
   as one agent given the split's actual spend (`run.py --match split`); and
2. the batch gains a wall-clock requirement, stated as a number, that one agent's
   sequential run misses and the split meets (on this task set: 34.9 s against 9.8 s).

Reopen **the single issue** only if a split beats one agent at the same spend by more
than the sweep-to-sweep spread (here one pass in 18).

This is slide 49's condition (a batch of separate issues, each with its own check,
run at once), made testable by someone else. My batch result speaks to it: run at
once, the batch was 3.6× faster than one agent at the same spend, and it did **not**
pass more (25/30 against 27/30). Condition 1 failed here; condition 2 has never held.

## What each choice loses

- **What I lose by not splitting.** Wall-clock on a batch (about 25 s a day here, more
  with bigger batches); the structural guarantee that the agent holding the writes
  cannot search and drift, and the agent searching cannot write (one agent gets this
  only by phases in code, which S-plus shows works but which a future change could
  erode); and a natural place to put a different model per role, which this
  assignment never tested.
- **What I would lose by splitting.** A coordinator, a contract, a wait graph and a
  shared board, each a new place to fail and to maintain, for no measured gain in
  passes; an investigator that, offered a smaller tool set beside a strict return tool,
  sent a broken search filter in 28 of 139 searches (0 of 119 in arm S, with the same
  tools); nobody seeing across issues in a batch (two reports of one bug); and a writer
  that can only be as good as one structured return, since whatever the return leaves
  out is lost.

ARCHITECTURE.md is unchanged: RepoMind is still one agent, and that is the result.
