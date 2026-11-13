"""The split's guards (checks/FORMATS.md, section 14.6): the three the replays call.

None of them coordinates anything. Each is there so that a failure between agents is
seen and stopped, not discovered later:

    WaitGraph      deadlock detection (slides 35 and 38): every wait on another agent
                   is an edge; a wait that closes a cycle is reported, with the agents
                   in it, and victim() names the worker to stop.
    SharedState    compare-and-set on state agents share (slides 34 and 37): a write
                   names the version it read, and is refused if the state moved since.
    check_combined the check of the combined result (slides 36 and 45): the end state
                   read from the tracker, against rules set before the run. It needs no
                   answer key, and it is the one check that catches silent divergence,
                   where every agent succeeds and the whole is wrong.

The split (ways/split.py) uses all three on every run, and the replays in
checks/check_06.py feed them the recorded deadlock, lost update and planted end
states.
"""

from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath

COORDINATORS = frozenset({"coordinator"})


# -- deadlock ---------------------------------------------------------------------------------------

class WaitGraph:
    """Who waits on whom. A wait that closes a cycle is a deadlock: nobody in the cycle
    can move, and no agent inside it can see that (slide 35). The code running them can."""

    def __init__(self, coordinators=COORDINATORS):
        self._edges: dict[str, dict[str, str]] = {}      # waiter -> {on: what}
        self._coordinators = frozenset(coordinators)
        self._stopped: set[str] = set()
        self._lock = threading.Lock()

    def wait(self, waiter: str, on: str, what: str = "") -> list[str] | None:
        """Record that `waiter` now waits on `on`. The cycle this wait closes, as the
        agents in it (starting with the waiter), or None."""
        waiter, on = str(waiter), str(on)
        with self._lock:
            self._edges.setdefault(waiter, {})[on] = what
            if waiter == on:
                return [waiter]
            path = self._path(on, waiter)
            return [waiter] + path[:-1] if path else None

    def release(self, waiter: str, on: str) -> None:
        """`waiter` has what it waited for from `on`: the edge goes."""
        with self._lock:
            self._edges.get(str(waiter), {}).pop(str(on), None)

    def stop(self, agent: str) -> None:
        """A stopped agent waits on nobody, and nobody can wait on it any longer."""
        agent = str(agent)
        with self._lock:
            self._stopped.add(agent)
            self._edges.pop(agent, None)
            for targets in self._edges.values():
                targets.pop(agent, None)

    def victim(self, cycle: list[str]) -> str:
        """The worker to stop to break the cycle: one of the cycle's agents, never the
        coordinator. The cycle starts with the agent whose wait closed it, the newest
        wait, so that one is stopped; the older waits keep their place."""
        workers = [a for a in cycle if a not in self._coordinators]
        if not workers:
            raise ValueError(f"a cycle of coordinators only, {cycle}: no worker to stop")
        return workers[0]

    def waiting(self) -> dict[str, list[str]]:
        with self._lock:
            return {w: sorted(on) for w, on in self._edges.items() if on}

    def _path(self, start: str, goal: str) -> list[str] | None:
        """A path of waits from start to goal, as the agents on it (both ends included)."""
        stack, seen = [(start, [start])], {start}
        while stack:
            node, path = stack.pop()
            if node == goal:
                return path
            for nxt in sorted(self._edges.get(node, {})):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, path + [nxt]))
        return None


# -- lost updates -----------------------------------------------------------------------------------

class SharedState:
    """State several agents share, path -> content, each path with a version.

    A write is compare-and-set: it names the version the agent read, and it is refused
    if anyone wrote the path since. That turns Activity 4's lost update (B writes from
    a read older than A's write, and A's fix is gone) into a refusal B must answer by
    reading again. A path never written reads as ("", 0)."""

    def __init__(self, initial: dict[str, str] | None = None):
        self._content: dict[str, str] = dict(initial or {})
        self._version: dict[str, int] = {path: 0 for path in self._content}
        self._lock = threading.Lock()
        self.history: list[dict] = []        # every read and write, in order, for the trace

    def read(self, agent: str, path: str) -> tuple[str, int]:
        """The content and its version."""
        with self._lock:
            content, version = self._content.get(path, ""), self._version.get(path, 0)
            self.history.append({"op": "read", "agent": agent, "path": path, "version": version})
            return content, version

    def write(self, agent: str, path: str, content: str, version: int) -> bool:
        """Compare-and-set: write only if `path` is still at `version`, the version the
        agent read. True when written; False when refused, leaving the content as it was."""
        with self._lock:
            current = self._version.get(path, 0)
            ok = current == version
            if ok:
                self._content[path] = content
                self._version[path] = current + 1
            self.history.append({"op": "write", "agent": agent, "path": path, "version": version,
                                 "current": current, "ok": ok})
            return ok

    def content(self, path: str) -> str:
        with self._lock:
            return self._content.get(path, "")

    def version(self, path: str) -> int:
        with self._lock:
            return self._version.get(path, 0)


# -- the combined result ----------------------------------------------------------------------------

LABELS = ("bug", "feature", "question", "docs")


def _names(text: str, file: str) -> bool:
    """A pull request names a file when its title or body holds the path or the file's name."""
    text = (text or "").lower()
    return file.lower() in text or PurePosixPath(file).name.lower() in text


def _repo_root() -> Path | None:
    root = Path(__file__).resolve().parent.parent / "fixtures" / "repo"
    return root if root.is_dir() else None


def _disclosure() -> str | None:
    try:
        from policy.hook import disclosure
        return disclosure() or None
    except Exception:            # no policy to read: the rule cannot be applied, and is skipped
        return None


def check_combined(task, state: dict) -> list[str]:
    """The combined result's violations of the task's rules; [] when it passes.

    `state` is harness.tasks.end_state(tracker, run_id, task): for each issue, the run's
    comments, the labels still on it, and its open pull requests. The rules are set
    before the run and need no answer key. For each of the task's issues:

      1. exactly one comment from the run, with a label line and a file line;
      2. the label is one of the four, and a question names no file while any other
         label names one (the triage rules);
      3. a named file exists in the repository;
      4. the comment carries the disclosure line after its first two lines (the policy);
      5. exactly one label on the issue, the comment's;
      6. one open pull request naming the comment's file when the file is not none,
         none when it is.
    """
    from harness.formats import normalize_path, parse_triage_comment

    out: list[str] = []
    repo = _repo_root()
    line = _disclosure()
    for n in task.issues:
        s = state.get(n, state.get(str(n))) if isinstance(state, dict) else None
        if not isinstance(s, dict):
            out.append(f"#{n}: no end state for this issue")
            continue
        comments = list(s.get("comments") or [])
        labels = sorted({str(x).strip().lower() for x in s.get("labels") or []})
        prs = list(s.get("prs") or [])
        if len(comments) != 1:
            out.append(f"#{n}: {len(comments)} comments from the run, not one")
            # without one comment there is no label or file to hold the rest to
            if len(labels) > 1:
                out.append(f"#{n}: {len(labels)} labels on the issue {labels}, not one")
            continue
        body = str(comments[0] or "")
        label, file = parse_triage_comment(body)
        if label is None or file is None:
            out.append(f"#{n}: the comment lacks a {'label' if label is None else 'file'} line")
            continue
        file = normalize_path(file) or "none"
        if label not in LABELS:
            out.append(f"#{n}: the comment's label {label!r} is not one of {list(LABELS)}")
        if label == "question" and file != "none":
            out.append(f"#{n}: a question names file {file}, where it names none")
        if label in LABELS and label != "question" and file == "none":
            out.append(f"#{n}: label {label} names no file")
        if file != "none" and repo is not None and not (repo / file).is_file():
            out.append(f"#{n}: the comment's file {file} does not exist in the repository")
        if line and not any(r.strip() == line for r in body.splitlines()[2:]):
            out.append(f"#{n}: the comment lacks the disclosure line after its first two lines")
        if labels != [label]:
            out.append(f"#{n}: labels on the issue {labels}, where the comment says [{label!r}]")
        if file == "none":
            if prs:
                out.append(f"#{n}: {len(prs)} pull requests, where the comment's file is none")
        elif len(prs) != 1:
            out.append(f"#{n}: {len(prs)} open pull requests, where the comment names {file}")
        elif not _names(f"{prs[0].get('title', '')}\n{prs[0].get('body', '')}", file):
            out.append(f"#{n}: the pull request does not name the comment's file {file}")
    return out


__all__ = ["WaitGraph", "SharedState", "check_combined"]
