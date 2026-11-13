"""Arm M: the split. A coordinator, in code, and two roles split by tool surface.

    coordinator    code, no model calls. It receives the task from the runner (one issue
                   or a batch of five), starts one investigator per issue (a batch's five
                   at once), checks every return against the contract and its rules,
                   puts each accepted finding on the shared board, starts one writer per
                   finding, and, after the last write, checks the combined end state
                   read from the tracker.
    investigator   reads only (split/roles.py): issues_read, code_search, code_read_file,
                   code_run_tests, and its return tool.
    writer         writes only: issues_label, pulls_open, issues_comment, each guarded
                   against the finding it was briefed with; the disclosure line is added
                   in code.

The five checks (slide 47), all on every run:
  1. the return contract (split/contract.schema.json), validated on every return, and a
     return that breaks it rejected and traced (worker_return lines);
  2. deadlock detection: every wait is a wait line and an edge in a WaitGraph; a cycle
     stops one worker (worker_stopped);
  3. the check of the combined result: the end state from the tracker against rules set
     before the run (combined_check);
  4. compare-and-set on shared state: the findings board and each write's slot
     (shared_write lines), on top of the registry's idempotency keys;
  5. one trace: every agent writes through ctx.agent(...)'s trace, so every line names
     its agent and its parent, and its tokens are its own.

The budget: the coordinator and every worker share one ceiling per run, ctx.budget
(B per issue). Each issue gets an equal part; its investigator gets 75% of it and its
writer 20%, and 5% is held back, so the shares add up to less than the run's budget
and no worker can spend another's. Each loop checks its share before every call.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from agent.loop import load_bounds
from harness.backends import DailyQuotaReached, RateLimited
from harness.client import BudgetExceeded
from harness.formats import normalize_path
from harness.tasks import end_state
from split.guards import SharedState, WaitGraph, check_combined
from split.roles import Share, finding_rules, investigate, load_contract_schema, write

COORDINATOR = "coordinator"
INVESTIGATOR_SHARE, WRITER_SHARE = 0.75, 0.20       # of each issue's part; 5% held back
INVESTIGATOR_STEPS, WRITER_STEPS = 10, 6
INVESTIGATOR_WALL_S, WRITER_WALL_S = 180.0, 120.0
HARNESS_STOPS = (BudgetExceeded, RateLimited, DailyQuotaReached)


class Coordinator:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lead = ctx.agent(COORDINATOR)
        self.graph = WaitGraph()
        self.board = SharedState({})
        self.schema = load_contract_schema()
        self.stops: dict[str, threading.Event] = {}
        self.errors: list[BaseException] = []
        self._lock = threading.Lock()
        issues = ctx.issues
        budget = ctx.budget or load_bounds().max_tokens * len(issues)
        self.per_issue = budget / len(issues)
        self.prompts = {"investigator": ctx.prompt("investigator.md"), "writer": ctx.prompt("writer.md")}
        self.lead.trace.event("shares", budget=budget, per_issue=round(self.per_issue),
                              investigator=round(self.per_issue * INVESTIGATOR_SHARE),
                              writer=round(self.per_issue * WRITER_SHARE),
                              held_back=round(self.per_issue * (1 - INVESTIGATOR_SHARE - WRITER_SHARE)),
                              issues=list(issues))

    # -- waits, and the deadlock guard ------------------------------------------------------------
    def wait(self, on: str, what: str, turn: int) -> None:
        self.lead.trace.event("wait", waiter=COORDINATOR, on=on, what=what, turn=turn)
        cycle = self.graph.wait(COORDINATOR, on, what)
        if cycle:
            victim = self.graph.victim(cycle)
            self.lead.trace.event("worker_stopped", worker=victim, reason=f"a cycle of waits: {' -> '.join(cycle)}")
            self.graph.stop(victim)
            if victim in self.stops:
                self.stops[victim].set()

    def done_waiting(self, on: str) -> None:
        self.graph.release(COORDINATOR, on)

    def worker(self, agent_id: str):
        agent = self.ctx.agent(agent_id, parent=COORDINATOR)
        with self._lock:
            self.stops[agent_id] = threading.Event()
        return agent

    # -- one issue: investigate, accept, write ------------------------------------------------------
    def one(self, issue: int) -> None:
        try:
            self._one(issue)
        except HARNESS_STOPS as exc:          # the run's ceiling, a rate limit, the quota: the runner records it
            with self._lock:
                self.errors.append(exc)
        except Exception as exc:              # a bug in a worker's loop: recorded, the other issues go on
            self.lead.trace.event("worker_error", issue=issue, error=f"{type(exc).__name__}: {exc}"[:500])
            with self._lock:
                self.errors.append(exc)

    def _one(self, issue: int) -> None:
        inv = self.worker(f"investigator-{issue}")
        self.wait(inv.id, "a finding", turn=0)
        share = Share(int(self.per_issue * INVESTIGATOR_SHARE), INVESTIGATOR_STEPS, INVESTIGATOR_WALL_S)
        contract = investigate(self.ctx, self.lead, inv, issue, share, system=self.prompts["investigator"],
                               schema=self.schema, accept=finding_rules(issue), stop=self.stops[inv.id])
        self.done_waiting(inv.id)
        if contract is None:
            self.lead.trace.event("unresolved", issue=issue, worker=inv.id,
                                  reason="no return was accepted: nothing is written for this issue")
            return
        r = contract["result"]
        finding = {"issue": issue, "label": r["label"], "file": normalize_path(r["file"]) or "none",
                   "evidence": r["evidence"], "change": r["change"], "status": contract["status"],
                   "could_not_determine": contract["could_not_determine"], "from": inv.id}
        path = f"finding/{issue}"
        _, version = self.board.read(COORDINATOR, path)
        ok = self.board.write(COORDINATOR, path, json.dumps(finding, sort_keys=True), version)
        self.lead.trace.event("shared_write", path=path, version=version, ok=ok, content=finding)
        if not ok:
            self.lead.trace.event("unresolved", issue=issue, worker=inv.id,
                                  reason="the finding changed on the board while it was being accepted")
            return
        wr = self.worker(f"writer-{issue}")
        self.wait(wr.id, "the label, the pull request and the comment", turn=1)
        share = Share(int(self.per_issue * WRITER_SHARE), WRITER_STEPS, WRITER_WALL_S)
        write(self.ctx, wr, issue, finding, self.board.version(path), self.board, share,
              system=self.prompts["writer"], stop=self.stops[wr.id])
        self.done_waiting(wr.id)

    # -- the whole task -------------------------------------------------------------------------------
    def run(self) -> None:
        issues = self.ctx.issues
        if len(issues) == 1:
            self.one(issues[0])
        else:
            with ThreadPoolExecutor(max_workers=len(issues)) as pool:
                list(pool.map(self.one, issues))
        if any(isinstance(e, (RateLimited, DailyQuotaReached)) for e in self.errors):
            raise next(e for e in self.errors if isinstance(e, (RateLimited, DailyQuotaReached)))
        # the check of the combined result: from the tracker, never from what the workers said
        state = end_state(self.ctx.tracker, self.ctx.run_id, self.ctx.task)
        violations = check_combined(self.ctx.task, state)
        self.lead.trace.event("combined_check", passed=not violations, violations=violations)
        if self.errors:
            raise self.errors[0]
        if violations:
            self.ctx.trace.end("escalated", detail="the combined check failed; handed to a person: "
                                                   + "; ".join(violations)[:900])


def run(ctx) -> None:
    Coordinator(ctx).run()
