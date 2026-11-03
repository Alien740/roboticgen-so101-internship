# 08 · SO-101 vision & manipulation framework

Vision-guided **pick & place**, **basket sorting**, **multi-object** clearing, **stacking** and
**pen drawing** for the SO-101 arm (lerobot) with an overhead USB camera — packaged so it can be
reproduced on another PC, by a person or by any AI assistant.

Everything robot-specific (port, camera, gripper limits, wrist quirks, calibrations, object
sizes) lives in one commented profile: [`profiles/my_so101/robot.yaml`](profiles/my_so101/robot.yaml).

## Quick start

```powershell
cd 08_so101_framework
powershell -ExecutionPolicy Bypass -File install.ps1      # Linux/macOS: bash install.sh
.\.venv\Scripts\Activate.ps1                              # Linux/macOS: source .venv/bin/activate
so101 doctor                                              # checks python, motors, camera, calibration
so101 doctor --measure                                    # once: measure this robot's quirks (moves the arm)
so101 calibrate table                                     # whenever the camera moved (~12 min)
so101 basket --trials 5                                   # or: pick-place, multi, stack --hover, draw
so101 report                                              # metrics -> runs/my_so101/report.md
```

## Commands

| Command | What it does |
|---|---|
| `so101 doctor [--measure]` | health check with a fix for every problem; `--measure` records wrist range, gripper, camera latency |
| `so101 calibrate table` | the arm self-calibrates the pixel → robot mapping with the cube (one handover) |
| `so101 calibrate camera` | lens intrinsics from a printed checkerboard (auto-captures, no window) |
| `so101 calibrate hsv --color X` | measure an object's colour under the current light |
| `so101 detect [colours]` | what the camera sees, with robot coordinates + annotated image |
| `so101 pick-place` · `basket` · `multi` · `stack` | manipulation tasks, every trial logged |
| `so101 draw` | draw shapes with a pen held across the jaws |
| `so101 report` | detection / grasp / success rates, position error, cycle time, failure reasons |
| `so101 agent start/wait/answer/status/stop` | let an AI drive any task (see AGENTS.md) |
| `so101 demo` | practise the prompt/answer protocol, nothing moves |

Options: `--profile NAME` (or `SO101_PROFILE`), `--agent` (answers via `runs/<profile>/cmd.txt`).

## Documentation

- [`AGENTS.md`](AGENTS.md) — the playbook for AI assistants (Claude, ChatGPT, local models): safety rules, protocol, who answers what
- [`docs/PROCEDURES.md`](docs/PROCEDURES.md) — what the person at the robot does, step by step
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — symptom → cause → fix (everything hit while building this)
- [`prompts/`](prompts/) — copy-paste prompts and a tool schema for ChatGPT / local models
- [`.claude/skills/`](.claude/skills/) — Claude Code skills for the same procedures

## Tests (no hardware)

```
python -m pytest -q
```

## Reference setup

Windows 11, Python 3.12.9, lerobot 0.6.2 (commit 8c920c42), SO-101 follower on a 5 V supply,
640×480 USB webcam looking down at the table. Linux and macOS are supported (camera backend and
serial port are auto-detected) but less tested.
