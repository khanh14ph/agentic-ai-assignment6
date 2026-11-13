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

- The coordinator and the roles, and the tools each holds:
- Why they split by tool surface:
- The return contract, field by field, and which fields come from the worker's tool calls:
- Each worker's share of the budget, and what is held back:
- The five checks, where each is in your code:

## Part C · Measured

**The five constants** (slide 59): model, tools, budget, evaluator, task set.

**The results.**

| Arm | Kind | Passes / runs | Lowest and highest sweep | Tasks all three passed | Mean tokens | Mean wall-clock (s) |
|---|---|---|---|---|---|---|
| S | single | | | | | |
| S | batch | | | | | |
| M | single | | | | | |
| M | batch | | | | | |
| S-plus | single | | | | | |
| S-plus | batch | | | | | |

- Your multiplier (M over S), single and batch, beside the lecture's 15×:
- Whether S-plus's spend matched M's in fact, task by task:

**Slide 18's three questions**, for each kind of task.

| Question | One issue | A batch of five |
|---|---|---|
| Did the split spend more? | | |
| Does spending more raise the score? | | |
| Given the same budget, did one agent do as well? | | |

- What you would have claimed from arms S and M alone, and what you claim now:

**Every arm M run that failed.**

| Run | What each agent read, wrote and waited on | The first thing that went wrong | Anomaly | MAST category |
|---|---|---|---|---|
| | | | | |

## Part D · The decision

- One issue:
- A batch of five:
- The condition that would reopen it:
- The record: `decisions/0006-topology.md`

## AI assistance

Which tool, what it wrote, and what you checked:
