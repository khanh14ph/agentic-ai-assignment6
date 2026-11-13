"""Arm S-plus: one agent, given what arm M actually spent on each task (slide 16's fair rival).

run.py --match split sets ctx.budget to arm M's mean spend on this task. One agent may
have nowhere to put that many tokens in one context, so it spends them as slide 16's
fair rival does: further read-only attempts, one after another, each in a fresh
context (which one agent can do more cheaply than a team, slide 12), until it has
spent about its budget; then a choice of answer by something it can compute without
the known answers, a vote over its attempts; then it writes the chosen answer, once.

It is one agent: nothing runs at once, there is no coordinator and there are no
roles. Each loop is traced as its own ctx.agent(...) only because an agent's trace
ends once (checks/FORMATS.md, section 14.3): `agent` is the agent, and its loops are
attempt-<k>-<issue> and write-<issue>. Its attempts use the same return tool, the same
code checks on an answer, and the same guarded writes as arm M, so S-plus and M
differ in topology, not in the checks.

The budget. Each issue's first attempt and its write get exactly the shares arm M
gives its investigator and its writer (75% and 20% of the task's ceiling per issue,
ways/split.py), with the same closing turns: so S-plus's first pass does the same
work under the same bounds as M's two workers, one after the other instead of in
two roles and, on a batch, at once. The matched budget, ctx.budget (arm M's mean spend
on the task), is then spent on further attempts: each issue gets an equal part of
what is left; another attempt starts only while what is left of the part, after the
write, would pay for most of another (three quarters of the attempts' mean cost), and
is capped at it; if no attempt has given an answer yet, one more is tried while at
least 40% of the part is left. A write is never cut short by the budget: a cut write
leaves an issue half-written, which no agent would choose.

This is the arm's third version, each measured on the task set and discarded with
--discard (runs/discarded/; reports/LAB_06.md, Part C). The first capped each
attempt at the issue's part minus a write reserve and held back two closing turns'
cost, so on single issues it stopped attempts before they had read a file and spent
48% to 78% of its budget. The second capped each batch issue's first attempt and its
write at an even fifth of the matched budget, which arm M's workers never were: it
cut writes before their comment after the vote had chosen an answer. Both were
defects in the rival's bookkeeping, seen in the traces and the spend, not in the
answers.
"""

from __future__ import annotations

from collections import Counter

from agent.loop import load_bounds
from harness.formats import normalize_path
from split.guards import SharedState
from split.roles import Share, finding_rules, investigate, load_contract_schema, write, writer_brief

AGENT = "agent"
MAX_ATTEMPTS = 5
ATTEMPT_STEPS, ATTEMPT_WALL_S = 10, 180.0
WRITE_STEPS, WRITE_WALL_S = 6, 120.0
WRITE_ESTIMATE = 6_000              # a write's cost before this run has made one (the dev runs: 5,500 to 6,300)
WRITE_MARGIN = 1.15
FURTHER_ATTEMPT = 0.75              # start another attempt only if what is left pays for this much of one
RESCUE_SHARE = 0.40                 # with no answer yet, one more attempt while this much of the part is left
CLOSING_TURNS = 1                   # the turn a further attempt holds back for its answer
FIRST_SHARE, WRITE_SHARE = 0.75, 0.20  # of the ceiling per issue: arm M's investigator and writer shares


def key(contract: dict) -> tuple[str, str]:
    r = contract["result"]
    return r["label"], normalize_path(r["file"]) or "none"


def vote(answers: list[dict]) -> tuple[dict | None, dict]:
    """The answer most attempts agree on (label and file); a tie goes to the answer with
    more `found` statuses, then to the earliest. Computed without the known answers."""
    if not answers:
        return None, {}
    counts = Counter(key(a) for a in answers)
    found = Counter(key(a) for a in answers if a.get("status") == "found")
    order = {k: i for i, k in reversed(list(enumerate(key(a) for a in answers)))}
    winner = max(counts, key=lambda k: (counts[k], found[k], -order[k]))
    chosen = next(a for a in answers if key(a) == winner and a.get("status") == "found") \
        if found[winner] else next(a for a in answers if key(a) == winner)
    return chosen, {f"{label} {file}": n for (label, file), n in counts.items()}


def run(ctx) -> None:
    me = ctx.agent(AGENT)
    schema = load_contract_schema()
    attempt_prompt, write_prompt = ctx.prompt("splus.md"), ctx.prompt("splus_write.md")
    board = SharedState({})
    issues = ctx.issues
    total = ctx.budget or load_bounds().max_tokens * len(issues)
    per_issue_ceiling = (ctx.ceiling or load_bounds().max_tokens * len(issues)) / len(issues)
    me.trace.event("shares", budget=total, issues=list(issues), max_attempts=MAX_ATTEMPTS)
    write_costs: list[int] = []
    for index, issue in enumerate(issues):
        start = ctx.client.total_tokens
        part = max(0, total - start) / (len(issues) - index)        # what is left, shared by the issues left
        reserve = WRITE_MARGIN * (sum(write_costs) / len(write_costs)) if write_costs else WRITE_ESTIMATE
        answers, costs = [], []
        for k in range(1, MAX_ATTEMPTS + 1):
            used = ctx.client.total_tokens - start
            if k == 1:
                cap = FIRST_SHARE * per_issue_ceiling               # the first attempt: M's investigator's share
            else:
                cap = part - reserve - used                         # later ones: what the write leaves
                further = cap >= FURTHER_ATTEMPT * (sum(costs) / len(costs))
                rescue = not answers and part - used >= RESCUE_SHARE * part
                if not (further or rescue):
                    break
                cap = max(cap, RESCUE_SHARE * part) if rescue and not further else cap
            attempt = ctx.agent(f"attempt-{k}-{issue}", parent=AGENT)
            share = Share(int(cap), ATTEMPT_STEPS, ATTEMPT_WALL_S)
            me.trace.event("attempt", issue=issue, attempt=k, cap=share.tokens, used_before=used,
                           part=round(part), write_reserve=round(reserve))
            answer = investigate(ctx, me, attempt, issue, share, system=attempt_prompt, schema=schema,
                                 accept=finding_rules(issue), event="attempt_answer", run_budget=None,
                                 final_turns=None if k == 1 else CLOSING_TURNS)
            costs.append(attempt.client.total_tokens)
            if answer is not None:
                answers.append(answer)
        chosen, counts = vote(answers)
        me.trace.event("vote", issue=issue, attempts=len(costs), answers=len(answers), counts=counts,
                       chosen=list(key(chosen)) if chosen else None, spent=ctx.client.total_tokens - start)
        if chosen is None:
            me.trace.event("unresolved", issue=issue, reason="no attempt gave an answer that passed the checks")
            continue
        r = chosen["result"]
        finding = {"issue": issue, "label": r["label"], "file": normalize_path(r["file"]) or "none",
                   "evidence": r["evidence"], "change": r["change"], "status": chosen["status"],
                   "could_not_determine": chosen["could_not_determine"]}
        path = f"finding/{issue}"
        _, version = board.read(AGENT, path)
        board.write(AGENT, path, repr(finding), version)
        writer = ctx.agent(f"write-{issue}", parent=AGENT)
        write(ctx, writer, issue, finding, board.version(path), board,
              Share(int(WRITE_SHARE * per_issue_ceiling), WRITE_STEPS, WRITE_WALL_S), system=write_prompt,
              run_budget=None,
              brief=writer_brief(issue, finding, source=f"Your answer, chosen by a vote over {len(answers)} "
                                                        f"of your attempts ({counts})"))
        write_costs.append(writer.client.total_tokens)
