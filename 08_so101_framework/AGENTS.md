# AGENTS.md — operating the SO-101 framework (for any AI assistant)

You are helping a person run a real **SO-101 robot arm** (lerobot, 6 Feetech STS3215 servos,
5 V supply) with an **overhead USB camera**. It does: camera calibration, vision-guided
pick-and-place, dropping objects into a basket, multi-object sorting, stacking, and pen drawing.
Everything runs through one command: `so101`. This file is the same for Claude, ChatGPT,
Cursor, Copilot, or a local model.

## 1. Golden rules (non-negotiable)

1. **The robot is real.** Never start a motion command without the human's go-ahead in this
   conversation. Read-only commands (`doctor`, `detect`, `report`, `profile show`) are always fine.
2. **Run `so101 doctor` first** — before any task, after any error, after any replug.
3. **Human-only answers.** Only the human can confirm physical facts. Never answer these yourself:
   - "is the TIP pointing straight down?", "both jaws clear?", "is the pen firm?"
   - "put the cube under the jaws / hold the pen / hold the arm" (needs their hands)
   - any height measurement ("how high is the tip?") or "touch" during a jog
4. **Stop is always allowed.** If anything looks wrong, or the human says stop: `so101 agent stop`
   (or Ctrl+C in a terminal). Stopping drops nothing; it aborts the motion and lifts clear.
5. **Overload = power cycle.** A motor reporting `overload` only clears when the human unplugs
   the arm's power for 10 s. Never retry around it.
6. **Settings live in the profile**, not in code: `profiles/<name>/robot.yaml`. Measured values
   go to `profiles/<name>/measured.yaml` (written by the tools).
7. **Report honestly.** A trial succeeded only if the tool logged `success=1` (the camera checked it).
   "It moved" is not success.

## 2. Map of the repository

| Path | What |
|---|---|
| `so101/` | the package (`cli.py` = all commands) |
| `profiles/my_so101/robot.yaml` | every robot/setup-specific value, commented with WHY |
| `profiles/my_so101/calibration/` | motor calibration (`my_so101.json`), table mapping, reference view |
| `runs/my_so101/` | session logs, trial CSVs, photos, `report.md`, `doctor_report.json` |
| `docs/TROUBLESHOOTING.md` | symptom → cause → fix for everything seen so far |
| `docs/PROCEDURES.md` | step-by-step for each task, what the human does |
| `prompts/` | ready-made prompts for chat-only models + a tool schema |
| `.claude/skills/` | Claude Code skills (same procedures) |

## 3. Two ways to drive a task

**A. You can run shell commands** (Claude Code, Cursor, a local model with tools):
```
so101 agent start basket --trials 3     # runs in the background
so101 agent wait                         # blocks until it asks something; prints JSON
so101 agent answer "go"                  # reply (only after the human confirmed, see rule 3)
so101 agent status                       # what is it doing / asking now
so101 agent stop                         # halt
```
`wait` returns `{"state": "waiting"|"running"|"ended", "prompt": ..., "options": [...], "events": [...]}`.
Loop: `wait` → relay the prompt to the human if it is human-only → `answer` → `wait` … until `ended`.
Practise first with `so101 agent start demo` (nothing moves).

**B. You cannot run commands** (ChatGPT web, a chat-only model): tell the human which command
to type, then ask them to paste the output back. In a terminal, `so101` prompts interactively
(`WAITING: ...` then `>`); the human types the answer. Every prompt also prints a one-line
`@@WAIT {...}` JSON and every event `@@EVENT {...}` — read those to know exactly where you are.

## 4. Standard order of work

1. **Install** (once per PC): `install.ps1` (Windows) or `install.sh` (Linux/macOS). Ends with `doctor`.
2. **Health**: `so101 doctor` → fix every `ERROR` with the printed fix, re-run until clean.
3. **Quirks** (once per robot, or after repairs): `so101 doctor --measure` (moves wrist + gripper;
   ask the human to clear the table first).
4. **Calibration** (after the camera moves; `doctor` warns "camera moved"):
   - `so101 calibrate table` — ~12 min, the human places the cube once. Target: hold-one-out median < 5 mm.
   - optional `so101 calibrate camera` (printed checkerboard), then `calibrate table` again.
   - new object colour / lighting: `so101 calibrate hsv --color NAME`, check with `so101 detect NAME`.
5. **Tasks**: `pick-place`, `basket`, `multi`, `stack` (`--hover` first), `draw`. See `docs/PROCEDURES.md`.
6. **Metrics**: `so101 report` → `runs/<profile>/report.md`.

## 5. Prompts you will see and who answers

| Prompt contains | Who answers | Typical answer |
|---|---|---|
| "put the … ON THE TABLE under the jaws", "hold the pen", "put the pen across the jaws" | human (hands) | `go` when they say done |
| "move the object(s) to new spots" | human | `go` |
| "Does the TIP point STRAIGHT DOWN?" | human (eyes) | `go`, `roll -10`, `flip`, `nudge 10 0` |
| "both jaws clear of its top?" (stack --hover) | human (eyes) | `go`, or stop and report |
| "how high is the pen TIP above the paper, in mm?" | human (ruler) | a number |
| "jog … or 'touch'" | human (eyes) | `down 5`/`down 3`/`down`/`up`/`touch` (max 5 mm per step) |
| "'draw <shape> <size_mm>' …" (drawing menu) | you may, if the human asked for that drawing | `level 60`, then `draw circle 60` |
| "MEASURE moves the arm…" | human | `go` / `no` |

Shapes: `square circle star spiral smiley house`, up to 60 mm. Draw `level <size>` before the first
large shape on a new spot (the paper height varies several mm).

## 6. Reading results and failures

Each task logs one CSV row per object/trial in `runs/<profile>/<task>_trials.csv` and an
`@@EVENT {"type": "trial", ...}` line. Key fields: `detected, attempts, grasped, grip_pos,
success, error_mm, cycle_time_s, failure`. Look up the `failure` text in
`docs/TROUBLESHOOTING.md`. Common ones:
- `out of reach: r = …` — object must be 15–33 cm from the base (drops up to 37 cm).
- `grasp missed xN (gripper 0.4)` — jaws closed on nothing: calibration drift → re-run `calibrate table`.
- `jaw jammed on object (gripper 46–55)` — jaw landed on top / gripper too weak.
- `camera moved … px` — put the camera back or recalibrate.
- `MOTOR FAULT … overload` — human power-cycles the arm.

## 7. Changing behaviour safely

- Tune values in `robot.yaml` (each has a comment saying why it is what it is). Typical:
  `gripper.torque`, `objects.<name>.grasp_z / torque / clearance`, `drawing.press / speed`,
  `tasks.pick_place.place_xy`, `basket.rim_clear`.
- Add an object: one line under `objects:` with `color, size [long, short] m, height, grasp_z,
  torque, max_aspect` (+ `align: true` for rectangles). Then `so101 multi --objects NAME`.
- New robot or second arm: copy `profiles/my_so101` to `profiles/<new>`, run lerobot's motor
  calibration for it and put `<id>.json` in its `calibration/`, then `doctor --measure` and
  `calibrate table`. Select with `--profile <new>` or `SO101_PROFILE=<new>`.
- Run `python -m pytest -q` (no hardware needed) after any code change.

## 8. Do not

- Do not hold the gripper closed on something for minutes (overload trip) — `quit` idle sessions.
- Do not kill a running task while the arm is stretched out: `stop` first (it lifts and parks);
  if you must restart, ask the human to support the arm (torque drops briefly on reconnect).
- Do not "fix" a failed grasp by lowering thresholds until it passes; find the cause.
- Do not invent results. If the camera did not confirm success, it did not succeed.
