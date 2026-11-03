"""`so101 report`: metrics for every task from runs/<profile>/*_trials.csv -> report.md.

Per task: trials, detection rate, grasp rate (of detected and in reach), full-task success,
position error (mean +- sd), cycle time, failure reasons ranked.
"""
from __future__ import annotations

import csv
from collections import Counter

import numpy as np


def summarise(rows):
    n = len(rows)
    det = [r for r in rows if r["detected"] == "1"]
    reach = [r for r in det if "out of reach" not in r["failure"]]
    grasped = [r for r in reach if r["grasped"] == "1"]
    ok = [r for r in rows if r["success"] == "1"]
    first = [r for r in grasped if r["attempts"] == "1"]
    err = [float(r["error_mm"]) for r in ok if r["error_mm"]]
    cyc = [float(r["cycle_time_s"]) for r in ok if r["cycle_time_s"]]
    pct = lambda a, b: f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"
    return {"trials": n, "detection": pct(len(det), n), "in reach": pct(len(reach), len(det)),
            "grasp": pct(len(grasped), len(reach)) + (f", {len(first)} first try" if grasped else ""),
            "success": pct(len(ok), n),
            "position error": f"{np.mean(err):.1f} +- {np.std(err):.1f} mm (n={len(err)})" if err else "-",
            "cycle time": f"{np.mean(cyc):.1f} +- {np.std(cyc):.1f} s" if cyc else "-",
            "failures": Counter(r["failure"] for r in rows if r["failure"]).most_common()}


def main(a, prof, sess):
    files = sorted(prof.run_dir.glob("*_trials.csv"))
    if not files:
        sess.say(f"no trial logs in {prof.run_dir} yet - run a task first"); return 1
    md = [f"# Trial report - profile `{prof.name}`", ""]
    for f in files:
        rows = list(csv.DictReader(open(f, newline="")))
        if not rows:
            continue
        s = summarise(rows)
        md += [f"## {f.stem.replace('_trials', '')}", "", "| metric | value |", "|---|---|"]
        md += [f"| {k} | {v} |" for k, v in s.items() if k != "failures"]
        if s["failures"]:
            md += ["", "Failure reasons:", ""] + [f"- {c} x {r}" for r, c in s["failures"]]
        md.append("")
        sess.event("report", task=f.stem, **{k: v for k, v in s.items() if k != "failures"})
    out = prof.run_dir / "report.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    sess.say(f"saved {out}")
