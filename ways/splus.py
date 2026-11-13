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

The budget is per task. Each issue gets an equal part of what is left; a reserve is
kept for the write; attempts go on while what is left would pay for at least half of
another (an attempt's cost is estimated from the attempts before it), and the last
attempt is capped at what is left, so the spend comes out at about the budget.
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
WRITE_RESERVE_SHARE = 0.20          # of an issue's part, held for the write ...
WRITE_RESERVE_MIN, WRITE_RESERVE_MAX = 6_000, 20_000   # ... within these bounds
MIN_ATTEMPT_TOKENS = 4_000          # an attempt with less than this cannot read an issue and a file


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
    me.trace.event("shares", budget=total, issues=list(issues), max_attempts=MAX_ATTEMPTS)
    for index, issue in enumerate(issues):
        start = ctx.client.total_tokens
        part = max(0, total - start) / (len(issues) - index)        # what is left, shared by the issues left
        reserve = min(max(WRITE_RESERVE_SHARE * part, WRITE_RESERVE_MIN), WRITE_RESERVE_MAX)
        answers, costs = [], []
        for k in range(1, MAX_ATTEMPTS + 1):
            left = part - reserve - (ctx.client.total_tokens - start)
            if k > 1 and (left < MIN_ATTEMPT_TOKENS or left < 0.5 * (sum(costs) / len(costs))):
                break
            attempt = ctx.agent(f"attempt-{k}-{issue}", parent=AGENT)
            share = Share(int(max(left, MIN_ATTEMPT_TOKENS)), ATTEMPT_STEPS, ATTEMPT_WALL_S)
            me.trace.event("attempt", issue=issue, attempt=k, cap=share.tokens, left_before=round(left))
            answer = investigate(ctx, me, attempt, issue, share, system=attempt_prompt, schema=schema,
                                 accept=finding_rules(issue), event="attempt_answer", run_budget=None)
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
        left = part - (ctx.client.total_tokens - start)
        writer = ctx.agent(f"write-{issue}", parent=AGENT)
        write(ctx, writer, issue, finding, board.version(path), board,
              Share(int(max(left, reserve)), WRITE_STEPS, WRITE_WALL_S), system=write_prompt, run_budget=None,
              brief=writer_brief(issue, finding, source=f"Your answer, chosen by a vote over {len(answers)} "
                                                        f"of your attempts ({counts})"))
