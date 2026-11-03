---
name: so101-report
description: Summarise SO-101 trial metrics (detection rate, grasp rate, success rate, position error, cycle time, failure reasons) for an assignment or progress report. Use after trials or when the user asks how well the robot performed.
---

# Metrics report

1. Run `so101 report`. It prints the metrics and writes `runs/<profile>/report.md`, one section per task.
2. Present: trials, detection rate, in-reach rate, grasp rate (and how many on the first try), task
   success, position error mean ± sd, cycle time, and the failure reasons ranked.
3. Be honest: if the CSV notes say a failure came from a software bug that was later fixed, keep it
   separate from genuine robot failures, but never drop rows.
4. Mention conditions not yet covered (lighting, clutter) and suggest trials for them.
