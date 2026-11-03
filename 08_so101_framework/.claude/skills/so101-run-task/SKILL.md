---
name: so101-run-task
description: Run SO-101 manipulation trials (pick-place, basket, multi-object, or stack) and collect metrics. Use when the user asks the arm to pick something up, put a cube in the basket, clear the table, stack the brick, or run N trials for an assignment.
---

# Run a manipulation task

Golden rules: AGENTS.md. Pre-flight: `so101 doctor` is clean (no "camera moved"; otherwise calibrate first).

| User wants | Command |
|---|---|
| cube to a fixed spot | `so101 agent start pick-place --trials N` |
| cube into the basket | `so101 agent start basket --trials N [--objects cube]` |
| clear several objects | `so101 agent start multi [--objects cube lego]` |
| brick over the cube | first `so101 agent start stack --hover`, then `so101 agent start stack` |

Before starting, tell the human the layout: objects 15-33 cm from the base, basket 20-34 cm,
at least 7 cm apart. Get their go-ahead, because the arm will move.

Loop on `so101 agent wait`:
- The prompt asks the human to move or place things, or to confirm something physical: relay it,
  wait for their reply, then `so101 agent answer go` (or what they said).
- `state: ended`: read the `trial` events and report each one: success, attempts, error_mm,
  cycle_time_s, failure. Look failures up in docs/TROUBLESHOOTING.md and suggest the fix.

If the human says stop, or anything looks wrong: `so101 agent stop`.
Afterwards offer `so101 report` (skill so101-report).
