---
name: so101-draw
description: Draw shapes (square, circle, star, spiral, smiley, house) on paper with a pen held in the SO-101 gripper. Use when the user wants the arm to draw or write.
---

# Pen drawing session

Golden rules: AGENTS.md. Almost every drawing prompt is **human-only** (their eyes and hands).
Prepare with the human: paper taped down about 22-34 cm in front of the base, felt-tip with the cap off.

1. `so101 agent start draw`, then `so101 agent wait`.
2. "put the pen ACROSS the jaws...": the human lays the pen across the jaws like a cigarette, tip
   out 5-7 cm, deep between the pads; then `answer go`. Next, "Let go, tug it...": `go` once they confirm.
3. "Does the TIP point STRAIGHT DOWN?": ask the human. Answers: `go`, `roll 10` / `roll -10`,
   `flip` (cap end is down), or `nudge <away_mm> <left_mm>`. The confirmed direction is saved.
4. "how high is the pen TIP...": send the human's number in mm.
5. Jog: relay `down 5` / `down 3` / `down` / `up` (at most 5 mm per step) until the human says it
   touches, then `touch`. Explain simply: "say down 5, down 3, down, and touch when it meets the paper".
6. At the menu: send `level 60` first (the human jogs 3 corners to `touch`), then
   `draw <shape> <size>` (at most 60 mm). Lines skipping: `press 3.5`. Wobbly: `speed 8`.
   New clean spot: `move 0 60`, then `probe`.
7. Send `quit` when done; don't leave the pen clamped while idle, or the gripper overloads. At the
   final prompt "hold the pen...", wait for the human to hold it, then `go`.

If a motor overload appears: stop, and have the human unplug the arm's power for 10 s.
