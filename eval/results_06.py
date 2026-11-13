#!/usr/bin/env python3
"""Assignment 6, Part C: every measured run's outcome, from the environment.

    python eval/results_06.py            # writes results/runs_06.jsonl and results/summary_06.json

One line per finished measured run (checks/FORMATS.md, section 14.7): a vendor's
model, on a fixtures/tasks6 task, with a run_end line and not ended quota or
rate_limited. `passed` and `parts` come from eval/check_task.py, which reads the
tracker (its database and its archives); `tokens` from run_end.totals.total_tokens;
`wall_s` from run_end.elapsed_s. Nothing is read from any agent's final message.

results/summary_06.json holds what reports/LAB_06.json's `arms`, `constants` and
`multiplier` are filled from, computed the way checks/check_06.py recomputes them,
plus what the report reads beside them: the pass rate per issue, the spend per task
for each arm, arm S-plus's budget against arm M's spend, and the arm M runs that
failed, with what check_task found wrong in each.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from checks.check_06 import TASK_DIR, Remote, load_runs  # noqa: E402  the checker's own readers
from eval.check_task import check_task  # noqa: E402
from harness.tasks import load_tasks  # noqa: E402

SWEEPS = 3
ARM_ORDER = ("S", "M", "S_plus")


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def rows(root: Path = ROOT) -> tuple[list[dict], dict, list]:
    tasks = {t.id: t for t in load_tasks(root / TASK_DIR)}
    runs, _ = load_runs(set(tasks), root)
    finished = [r for r in runs if r.finished and r.arm]
    remote = Remote(root)
    out, details = [], {}
    for run in finished:
        checked = check_task(run.run_id, tasks[run.task], tracker=remote)
        details[run.run_id] = checked["details"]
        out.append({"run_id": run.run_id, "arm": run.arm, "way": run.way, "task": run.task, "kind": run.kind,
                    "sweep": run.sweep, "passed": checked["passed"],
                    "parts": {str(k): v for k, v in checked["parts"].items()},
                    "tokens": run.tokens, "wall_s": run.wall_s,
                    "budget": run.start.get("budget"), "ceiling": run.start.get("ceiling"),
                    "started_at": run.start.get("started_at")})
    out.sort(key=lambda r: (ARM_ORDER.index(r["arm"]), r["kind"], r["task"], r["sweep"], r["started_at"] or ""))
    return out, details, finished


def summarize(results: list[dict]) -> dict:
    """The arms, as section 14.8 names their fields, and as checks/check_06.py recomputes them."""
    arms = {}
    for arm in ARM_ORDER:
        arms[arm] = {}
        for kind in ("single", "batch"):
            mine = [r for r in results if r["arm"] == arm and r["kind"] == kind]
            per_task, sweeps = {}, {}
            for r in mine:
                per_task[r["task"]] = per_task.get(r["task"], 0) + int(r["passed"])
                sweeps[r["sweep"]] = sweeps.get(r["sweep"], 0) + int(r["passed"])
            tasks = sorted({r["task"] for r in mine})
            arms[arm][kind] = {
                "runs": len(mine), "passes": sum(int(r["passed"]) for r in mine),
                "per_task": dict(sorted(per_task.items())),
                "sweep_min": min(sweeps.values()) if sweeps else 0,
                "sweep_max": max(sweeps.values()) if sweeps else 0,
                "all_three": sum(1 for t in tasks if per_task.get(t, 0) >= SWEEPS),
                "mean_tokens": round(mean(r["tokens"] for r in mine)),
                "mean_wall_s": round(mean(r["wall_s"] for r in mine), 1)}
    return arms


def extras(results: list[dict], details: dict, arms: dict) -> dict:
    """What the report reads beside the arms."""
    out: dict = {"multiplier": {}, "per_issue": {}, "spend_per_task": {}, "splus_match": {}, "m_failures": []}
    for kind in ("single", "batch"):
        s, m = arms["S"][kind]["mean_tokens"], arms["M"][kind]["mean_tokens"]
        out["multiplier"][kind] = round(m / s, 2) if s else None
    for arm in ARM_ORDER:
        for kind in ("single", "batch"):
            parts = [v for r in results if r["arm"] == arm and r["kind"] == kind for v in r["parts"].values()]
            out["per_issue"].setdefault(arm, {})[kind] = {"passed": sum(parts), "of": len(parts)}
    for task in sorted({r["task"] for r in results}):
        out["spend_per_task"][task] = {
            arm: {"mean_tokens": round(mean(r["tokens"] for r in results if r["arm"] == arm and r["task"] == task)),
                  "passes": sum(int(r["passed"]) for r in results if r["arm"] == arm and r["task"] == task),
                  "mean_wall_s": round(mean(r["wall_s"] for r in results if r["arm"] == arm and r["task"] == task), 1)}
            for arm in ARM_ORDER}
        sp = [r for r in results if r["arm"] == "S_plus" and r["task"] == task]
        m_mean = out["spend_per_task"][task]["M"]["mean_tokens"]
        if sp:
            out["splus_match"][task] = {
                "budget": sorted({r["budget"] for r in sp}), "m_mean_now": m_mean,
                "splus_mean": round(mean(r["tokens"] for r in sp)),
                "splus_over_m": round(mean(r["tokens"] for r in sp) / m_mean, 2) if m_mean else None}
    for r in results:
        if r["arm"] == "M" and not r["passed"]:
            out["m_failures"].append({"run_id": r["run_id"], "task": r["task"],
                                      "wrong": [{"issue": d["issue"], "why": d["why"], "label": d["label"],
                                                 "file": d["file"]} for d in details[r["run_id"]] if not d["passed"]]})
    return out


def main() -> int:
    results, details, finished = rows()
    (ROOT / "results").mkdir(exist_ok=True)
    with (ROOT / "results" / "runs_06.jsonl").open("w") as fh:
        for r in results:
            fh.write(json.dumps({k: v for k, v in r.items() if k not in ("budget", "ceiling", "started_at")}
                                | {"budget": r["budget"]}) + "\n")
    arms = summarize(results)
    constants = sorted({json.dumps(r.start.get("constants"), sort_keys=True) for r in finished})
    summary = {"constants": json.loads(constants[0]) if len(constants) == 1 else [json.loads(c) for c in constants],
               "arms": arms, **extras(results, details, arms)}
    (ROOT / "results" / "summary_06.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{len(results)} finished measured runs -> results/runs_06.jsonl, results/summary_06.json")
    for arm in ARM_ORDER:
        for kind in ("single", "batch"):
            a = arms[arm][kind]
            print(f"  {arm:<6} {kind:<6} {a['passes']:>2}/{a['runs']:<2} sweeps {a['sweep_min']}-{a['sweep_max']} "
                  f"all-three {a['all_three']}  mean {a['mean_tokens']:>9,} tok  {a['mean_wall_s']:>6} s")
    print(f"  multiplier (M over S): {summary['multiplier']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
