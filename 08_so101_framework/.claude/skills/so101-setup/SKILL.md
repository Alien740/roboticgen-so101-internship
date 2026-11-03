---
name: so101-setup
description: Install the SO-101 framework on a PC and verify the robot and camera. Use when the user sets up a new or reinstalled PC, says the arm or camera does not work, or before the first run of the day. Runs install, `so101 doctor`, and (with consent) `so101 doctor --measure`.
---

# SO-101 setup and health check

Follow the golden rules in AGENTS.md. Work from the `08_so101_framework` folder. If the venv is
not active, call the CLI as `.venv/Scripts/so101` (Windows) or `.venv/bin/so101`.

1. **Install** (skip if `.venv` exists and `so101 doctor` runs):
   - Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`
   - Linux/macOS: `bash install.sh` (Linux: the user must be in the `dialout` group)
2. **Ask the human** to plug in the arm USB **and** its power supply, and the camera.
3. Run `so101 doctor` (read-only). For every `[ERROR]`, tell the human the printed `->` fix in
   plain words, wait for them, re-run. Repeat until there are no errors. The intrinsics warning is
   optional. The JSON report is `runs/<profile>/doctor_report.json`.
4. **Quirk measurement** (once per robot, or after repairs). Ask: "This moves the wrist and gripper
   and makes a small pan move. Is the area around the arm clear?" Only on a yes:
   `so101 agent start doctor --measure`, then `so101 agent wait`; relay the confirmation prompt and
   `so101 agent answer go` only after the human says go. Summarise the `measure` lines; if the free
   wrist range differs from the profile, propose the edit to `profiles/<name>/robot.yaml`.
5. If `doctor` says the camera moved, use the `so101-calibrate` skill (table calibration).

Report: what is OK, what was fixed, and anything still needing the human.
