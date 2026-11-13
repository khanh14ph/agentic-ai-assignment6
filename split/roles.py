"""The split's two roles, each a loop with its own tool surface (Part B, task 1).

    investigator   issues_read, code_search, code_read_file, code_run_tests, and a local
                   return tool. It reads and returns a finding; it can write nothing.
    writer         issues_label, pulls_open, issues_comment, each guarded in code against
                   the accepted finding. It writes; it can read nothing.

The roles are split by tool surface, not by persona: what an agent is offered is what
it can do wrong. A role is offered only its own schemas, and a name outside them is
refused here, before the registry, so it never counts as the registry's protocol
error. Every call that is made still goes through the run's registry and its hook
(policy/hook.py): the policy and the write keys hold for every agent.

Each loop is Assignment 1's loop (agent/loop.py) with two changes: it ends when the
role's job is done (a return accepted, a comment posted) rather than waiting for the
model to stop, and it holds its last two turns in reserve, in which an investigator
must return (and may fix a rejected return once). Every model and tool line goes through the agent's own trace, so it
names the agent and its parent; the worker's tokens are its client's.

The contract's `searched` and `calls_used` are facts about the run: `Facts` records
them from the tool_call lines the worker's loop writes, never from the model's words.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from harness.client import ToolCall, tool_result
from harness.formats import normalize_path, parse_triage_comment
from harness.roles import arg, role_of, tool_for
from harness.schema import errors as schema_errors
from tools import ToolResult

ROOT = Path(__file__).resolve().parent.parent
RETURN_TOOL = "return_finding"
INVESTIGATOR_ROLES = ("read_issue", "search", "read_file", "run_tests")
WRITER_ROLES = ("apply_label", "open_pr", "comment")
MAX_REJECTIONS = 3           # returns sent back before the coordinator gives up on a worker
FINAL_TURNS = 2              # turns held back at the end of an investigator's share: a return, and one retry
NEXT_CALL_MARGIN = 1.15      # the next call costs about the last one, a little more: the context only grows

FINAL_TURN = ("Stop investigating now: you are at the end of your steps or your share of the budget. "
              "Call return_finding once with your best answer. Use status out_of_budget if you could not settle it, "
              "and list what you could not determine.")


def load_contract_schema() -> dict:
    return json.loads((ROOT / "split" / "contract.schema.json").read_text())


def return_tool(schema: dict) -> dict:
    """The worker's return tool: the contract's fields the model fills. `searched` and
    `calls_used` are left out: the coordinator's code adds them from the worker's calls."""
    props = schema["properties"]
    mine = ("result", "status", "not_searched", "could_not_determine")
    return {
        "name": RETURN_TOOL,
        "description": ("Return your finding to the coordinator, once, as your last call. It is checked in code: "
                        "a return that breaks the schema or the rules comes back to you with the reason. "
                        "Do not report what you searched or how many calls you made: the harness records both."),
        "strict": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": list(mine),
                         "properties": {k: _strip(props[k]) for k in mine}},
    }


def _strip(schema):
    """A schema without the keys a vendor's strict mode may not accept (title)."""
    if isinstance(schema, dict):
        return {k: _strip(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip(v) for v in schema]
    return schema


@dataclass
class Share:
    """One agent's share of the run: tokens (its own client's), model calls, seconds."""
    tokens: int
    steps: int
    wall_s: float


@dataclass
class Facts:
    """What a worker did, from its own traced tool calls: the contract's `searched` and
    `calls_used`, exactly as checks/check_06.py recomputes them from the trace."""
    calls: list[tuple[str, dict]] = field(default_factory=list)
    read_ok: set = field(default_factory=set)       # paths read without an error

    def add(self, name: str, tool_input: dict, result) -> None:
        self.calls.append((name, dict(tool_input or {})))
        if role_of(name) == "read_file" and not result.is_error:
            path = (tool_input or {}).get(arg("read_file", "path"))
            if path is not None:
                self.read_ok.add(normalize_path(str(path)))

    @property
    def calls_used(self) -> int:
        return sum(1 for name, _ in self.calls if role_of(name))

    @property
    def searched(self) -> list[str]:
        out: list[str] = []
        for name, inp in self.calls:
            role = role_of(name)
            value = inp.get(arg("search", "query")) if role == "search" else \
                inp.get(arg("read_file", "path")) if role == "read_file" else None
            if value is not None and str(value) not in out:
                out.append(str(value))
        return out


class Surface:
    """A role's tool surface: anything with .schemas and .call(name, input). Offered only
    the role's schemas; a name outside them is refused before the registry."""

    def __init__(self, registry, roles: tuple[str, ...]):
        self.registry = registry
        self.names = {tool_for(r) for r in roles}
        self.schemas = [s for s in registry.schemas if s["name"] in self.names]

    def allows(self, name: str) -> bool:
        return name in self.names

    def call(self, name: str, tool_input: dict, **kwargs):
        if not self.allows(name):
            return ToolResult(f"{name} is not one of this role's tools: {sorted(self.names)}", True, 0.0)
        return self.registry.call(name, tool_input, **kwargs)


def _refused(agent, step: int, call: ToolCall, reason: str) -> dict:
    """A call refused in code, before the registry: traced as an event, not a tool_call
    (it never reached a tool), and answered as an error the model can act on."""
    agent.trace.event("refused_call", step=step, name=call.name, input=call.input, reason=reason)
    return tool_result(call, reason, True)


def _bounded(agent, run_client, run_budget, share: Share, step: int, started: float, last_cost: int,
             reserve_turns: int) -> str | None:
    """Why the agent may not make its next call while keeping `reserve_turns` calls in
    hand, or None. Checked in code before every call; the model is never asked."""
    next_cost = int(last_cost * NEXT_CALL_MARGIN)
    if step + reserve_turns >= share.steps:
        return "turn_limit"
    if agent.client.total_tokens + next_cost * (1 + reserve_turns) > share.tokens:
        return "token_budget"
    if run_budget and run_client.total_tokens + next_cost * (1 + reserve_turns) > run_budget:
        return "token_budget"
    if time.monotonic() - started >= share.wall_s:
        return "wall_clock"
    return None


# -- the investigator ---------------------------------------------------------------------------------

def investigate(ctx, receiver, agent, issue: int, share: Share, *, system: str, schema: dict, accept,
                stop: threading.Event | None = None, event: str = "worker_return",
                run_budget: int | None | str = "ctx") -> dict | None:
    """One investigator's loop on one issue. Returns the accepted contract, or None.

    Every return is recorded on the receiver's trace as it arrives (`event`, a
    worker_return line for the split), with the schema's verdict (`valid`, `errors`)
    and the receiver's rules' verdict (`accepted`, `problems`), before anything acts on
    it. A return that breaks either goes back to the worker with the reason; nothing
    is repaired. `accept(contract, facts)` is the receiver's rules: a list of problems.
    `run_budget` is the run's total the loop also keeps under: ctx.budget unless given."""
    run_budget = ctx.budget if run_budget == "ctx" else run_budget
    surface = Surface(ctx.tools, INVESTIGATOR_ROLES)
    tools = surface.schemas + [return_tool(schema)]
    messages = [{"role": "user", "content": f"Investigate issue #{issue} (issue-{issue}) and return your finding."}]
    facts = Facts()
    step, last_cost, rejections, final = 0, 0, 0, False
    started = time.monotonic()
    while True:
        if stop is not None and stop.is_set():
            agent.trace.end("stopped", "the coordinator stopped this worker")
            return None
        why = _bounded(agent, ctx.client, run_budget, share, step, started, last_cost, reserve_turns=FINAL_TURNS)
        if why and not final:
            final = True                     # the reserved turn: return now, with what you have
            _say(messages, FINAL_TURN)
        if why and final:
            hard = _bounded(agent, ctx.client, run_budget, share, step, started, last_cost, reserve_turns=0)
            if hard and step > 0:
                agent.trace.end(hard, f"no accepted return after {step} model calls")
                return None
        step += 1
        response = agent.client.create(system=system, messages=messages, tools=tools)
        agent.trace.model_call(step, response)
        last_cost = response.usage.total_tokens
        calls = response.tool_calls
        if not calls:
            if final:
                agent.trace.end("model_stopped", "ended its turn without returning")
                return None
            final = True
            messages.append(response.assistant_message())
            _say(messages, "You ended your turn without returning. " + FINAL_TURN)
            continue
        messages.append(response.assistant_message())
        results, returned = [], None
        for call in [c for c in calls if c.name != RETURN_TOOL]:     # reads first, so the facts include them
            if final:
                results.append(_refused(agent, step, call, "Only return_finding is allowed now. " + FINAL_TURN))
                continue
            if not surface.allows(call.name):
                results.append(_refused(agent, step, call, f"{call.name} is not one of your tools; you only read."))
                continue
            result = surface.call(call.name, call.input)
            agent.trace.tool_call(step, call, result)
            facts.add(call.name, call.input, result)
            results.append(tool_result(call, result.content, result.is_error))
        for i, call in enumerate(c for c in calls if c.name == RETURN_TOOL):
            if i > 0:
                results.append(_refused(agent, step, call, "Only one return per turn."))
                continue
            contract = dict(call.input) if isinstance(call.input, dict) else {"value": call.input}
            contract["searched"] = facts.searched           # facts about the run, from its calls
            contract["calls_used"] = facts.calls_used
            errs = schema_errors(contract, schema)
            problems = [] if errs else list(accept(contract, facts))
            receiver.trace.event(event, worker=agent.id, contract=contract, valid=not errs, errors=errs,
                                 accepted=not errs and not problems, problems=problems)
            if not errs and not problems:
                returned = contract
                results.append(tool_result(call, "Accepted."))
            else:
                rejections += 1
                why_not = "; ".join((errs or problems)[:6])
                results.append(tool_result(call, f"Rejected, and not acted on: {why_not}. Fix it and call "
                                                 f"{RETURN_TOOL} again.", True))
        if returned is not None:
            agent.trace.end("model_stopped", f"returned a finding, accepted, after {step} model calls")
            return returned
        if rejections >= MAX_REJECTIONS:
            agent.trace.end("tool_error", f"{rejections} returns rejected")
            return None
        messages.append({"role": "user", "content": results})


def _say(messages: list[dict], text: str) -> None:
    """Add a user instruction: into the last user turn (after its tool results), or as a new turn."""
    last = messages[-1] if messages else None
    if last and last["role"] == "user" and isinstance(last["content"], list):
        last["content"].append({"type": "text", "text": text})
    elif last and last["role"] == "user" and isinstance(last["content"], str):
        last["content"] = last["content"] + "\n\n" + text
    else:
        messages.append({"role": "user", "content": text})


def finding_rules(issue: int, repo_root: Path | None = None):
    """The coordinator's rules for a return that fits the schema, set before the run and
    needing no answer key: its issue, the label/file pairing, and that a named file
    exists and that this worker read it (a fact from its own calls)."""
    repo = Path(repo_root or ROOT / "fixtures" / "repo")

    def rules(contract: dict, facts: Facts) -> list[str]:
        r = contract["result"]
        out = []
        if r["issue"] != int(issue):
            out.append(f"result.issue is {r['issue']}, and you were asked about issue {issue}")
        label, file = r["label"], normalize_path(r["file"]) or "none"
        if label == "question" and file != "none":
            out.append(f"a question names file none, not {file}")
        if label != "question" and file == "none":
            out.append(f"a {label} names the file responsible, not none")
        if file != "none":
            if not (repo / file).is_file():
                out.append(f"{file} is not a file in the repository: name the path exactly as the search shows it")
            elif file not in facts.read_ok:
                out.append(f"you have not read {file} with code_read_file: read it before you name it")
        return out

    return rules


# -- the writer ---------------------------------------------------------------------------------------

class GuardedWrites:
    """The writer's surface: the three writes, each checked in code against the finding
    the writer was briefed with, before the registry.

    - every write names the writer's own issue;
    - the finding on the shared board is still at the version the writer read (if the
      coordinator changed it since, every write is refused);
    - the label is the finding's; the pull request (only when the file is not none)
      is from repomind/issue-<n> and names the file; the comment comes last, after the
      label and the pull request, and its first two lines are the finding's;
    - each write first claims its slot on the board (label/<n>, pr/<n>, comment/<n>)
      by compare-and-set from the version it read, so a second writer, or a second call
      in one turn, is refused rather than written twice;
    - the disclosure line is added in code (ways.agent.with_disclosure), the one
      function every comment passes before the registry."""

    def __init__(self, ctx, agent, issue: int, finding: dict, version: int, board):
        from ways.agent import disclosure_line
        self.ctx, self.agent, self.issue, self.finding, self.version, self.board = \
            ctx, agent, int(issue), finding, version, board
        self.line = disclosure_line()
        self.tools = {r: tool_for(r) for r in WRITER_ROLES}
        roles = WRITER_ROLES if finding["file"] != "none" else ("apply_label", "comment")
        self.surface = Surface(ctx.tools, roles)
        self.schemas = self.surface.schemas
        self.done: set[str] = set()

    def _slot(self, role: str) -> str:
        return f"{role}/{self.issue}"

    def check(self, name: str, args: dict) -> str | None:
        role = role_of(name)
        if not self.surface.allows(name):
            return f"{name} is not one of your tools here: {sorted(self.surface.names)}"
        if str(args.get(arg(role, "number"), "")).strip() != f"issue-{self.issue}":
            return f"you write only on issue-{self.issue}"
        _, now = self.board.read(self.agent.id, f"finding/{self.issue}")
        if now != self.version:
            return f"the finding changed since your brief (version {self.version}, now {now}): stop"
        label, file = self.finding["label"], self.finding["file"]
        if role == "apply_label":
            if str(args.get(arg("apply_label", "label"), "")).strip().lower() != label:
                return f"the finding's label is {label}"
        elif role == "open_pr":
            if str(args.get(arg("open_pr", "branch"), "")).strip() != f"repomind/issue-{self.issue}":
                return f"the branch is repomind/issue-{self.issue}"
            text = f"{args.get(arg('open_pr', 'title'), '')}\n{args.get(arg('open_pr', 'body'), '')}".lower()
            if file.lower() not in text:
                return f"the pull request's title or body must name {file}"
        elif role == "comment":
            missing = [r for r in ("apply_label",) + (("open_pr",) if file != "none" else ()) if r not in self.done]
            if missing:
                return f"the comment comes last: first {' and '.join(missing)}"
            got = parse_triage_comment(str(args.get(arg("comment", "body"), "")))
            if got != (label, file):
                return f"the comment's first two lines must be 'label: {label}' and 'file: {file}', not {got}"
        return None

    def claim(self, role: str) -> str | None:
        """Compare-and-set on the role's slot: refused if it is done, or if anyone wrote it
        since this writer read it."""
        slot = self._slot(role)
        content, version = self.board.read(self.agent.id, slot)
        if content.startswith("done"):
            self.agent.trace.event("shared_write", path=slot, version=version, ok=False, content=content)
            return f"{role} on issue-{self.issue} is already done ({content}); do not repeat it"
        ok = self.board.write(self.agent.id, slot, f"claimed:{self.agent.id}", version)
        self.agent.trace.event("shared_write", path=slot, version=version, ok=ok, content=f"claimed:{self.agent.id}")
        return None if ok else f"another agent wrote {slot} since you read it; do not repeat it"

    def settle(self, role: str, ok: bool) -> None:
        slot = self._slot(role)
        content, version = self.board.read(self.agent.id, slot)
        value = f"done:{self.agent.id}" if ok else ""
        written = self.board.write(self.agent.id, slot, value, version)
        self.agent.trace.event("shared_write", path=slot, version=version, ok=written, content=value)
        if ok:
            self.done.add(role)

    def call(self, step: int, call: ToolCall) -> tuple[dict, bool]:
        """(the tool_result block, whether the call reached the registry)."""
        args = dict(call.input or {})
        refused = self.check(call.name, args)
        role = role_of(call.name)
        if refused is None:
            refused = self.claim(role)
        if refused is not None:
            return _refused(self.agent, step, call, refused), False
        if role == "comment":
            from ways.agent import with_disclosure
            body = arg("comment", "body")
            args[body] = with_disclosure(args.get(body, ""), self.line)
        sent = ToolCall(id=call.id, name=call.name, input=args)
        result = self.ctx.tools.call(call.name, args)
        self.agent.trace.tool_call(step, sent, result)
        self.settle(role, not result.is_error)
        return tool_result(call, result.content, result.is_error), True

    @property
    def complete(self) -> bool:
        return "comment" in self.done


def writer_brief(issue: int, finding: dict,
                 source: str = "The investigator's finding, accepted by the coordinator") -> str:
    sure = finding.get("status") == "found"
    lines = [f"Issue #{issue} (write it as issue-{issue}). {source}:",
             f"label: {finding['label']}", f"file: {finding['file']}",
             f"evidence: {finding.get('evidence', '')}", f"change: {finding.get('change', '')}"]
    if not sure:
        unsure = "; ".join(finding.get("could_not_determine") or []) or "the investigator did not settle it"
        lines.append(f"The finding is not certain (status {finding.get('status')}): {unsure}.")
    return "\n".join(lines)


def write(ctx, agent, issue: int, finding: dict, version: int, board, share: Share, *, system: str,
          stop: threading.Event | None = None, run_budget: int | None | str = "ctx",
          brief: str | None = None) -> bool:
    """One writer's loop on one issue: the label, the PR when the file is not none, and
    the comment, from the finding. True when the comment was posted."""
    run_budget = ctx.budget if run_budget == "ctx" else run_budget
    surface = GuardedWrites(ctx, agent, issue, finding, version, board)
    messages = [{"role": "user", "content": brief or writer_brief(issue, finding)}]
    step, last_cost = 0, 0
    started = time.monotonic()
    while True:
        if stop is not None and stop.is_set():
            agent.trace.end("stopped", "the coordinator stopped this worker")
            return surface.complete
        why = _bounded(agent, ctx.client, run_budget, share, step, started, last_cost, reserve_turns=0)
        if why and step > 0:
            agent.trace.end(why, f"after {step} model calls; done: {sorted(surface.done)}")
            return surface.complete
        step += 1
        response = agent.client.create(system=system, messages=messages, tools=surface.schemas)
        agent.trace.model_call(step, response)
        last_cost = response.usage.total_tokens
        if not response.tool_calls:
            agent.trace.end("model_stopped", f"done: {sorted(surface.done)}")
            return surface.complete
        messages.append(response.assistant_message())
        results = []
        for call in response.tool_calls:
            block, _ = surface.call(step, call)
            results.append(block)
        if surface.complete:
            agent.trace.end("model_stopped", "the comment is posted: the writer's job is done")
            return True
        messages.append({"role": "user", "content": results})


__all__ = ["investigate", "write", "finding_rules", "load_contract_schema", "return_tool", "Share", "Facts",
           "Surface", "GuardedWrites", "writer_brief", "RETURN_TOOL"]
