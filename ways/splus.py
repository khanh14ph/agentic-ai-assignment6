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

The budget is per task. Each issue gets an equal part of what is left. The first
attempt may use the whole part, holding back one closing turn for its answer: it is
the single agent's own investigation, and cutting it short to save for the write
would only waste what it had spent. The write then costs what writes have cost in
this run (6,000 tokens before the first). Further attempts start only while what is
left after the write would pay for most of another (three quarters of the attempts'
mean cost), each capped at what is left; if no attempt has given an answer yet, one
more is tried while at least 40% of the part is left. So the spend comes out at about
the budget, and an attempt is rarely started only to be cut off.

The first measurement of this arm used the write reserve on the first attempt too,
and the split's loop's two-turn closing reserve: on single issues it spent 48% to 78%
of its budget, telling attempts to stop before they had read a file. Those runs are in
runs/discarded/ (see reports/LAB_06.md, Part C).
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
CLOSING_TURNS = 1                   # the turn an attempt holds back for its answer


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
    write_costs: list[int] = []
    for index, issue in enumerate(issues):
        start = ctx.client.total_tokens
        part = max(0, total - start) / (len(issues) - index)        # what is left, shared by the issues left
        reserve = WRITE_MARGIN * (sum(write_costs) / len(write_costs)) if write_costs else WRITE_ESTIMATE
        answers, costs = [], []
        for k in range(1, MAX_ATTEMPTS + 1):
            used = ctx.client.total_tokens - start
            if k == 1:
                cap = part                                          # the first attempt: the whole part
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
                                 final_turns=CLOSING_TURNS)
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
        write_costs.append(writer.client.total_tokens)
