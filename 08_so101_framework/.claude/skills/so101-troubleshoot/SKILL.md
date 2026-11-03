---
name: so101-troubleshoot
description: Diagnose SO-101 problems such as the arm not connecting, motor overload, a black, frozen or moved camera, missed grasps, objects not detected, or drawing lines skipping. Use when a task failed or the user reports odd robot behaviour.
---

# Troubleshooting

1. Run `so101 doctor` (read-only) and read `runs/<profile>/doctor_report.json`.
2. Read the latest session: `runs/<profile>/session.log` (the `@@EVENT` lines near the end,
   especially `trial`, `grasp_result`, `error` and `stopped`) and `runs/<profile>/agent.out`.
3. Match the symptom in `docs/TROUBLESHOOTING.md` (sections: connection, motor faults, camera,
   detection, calibration, grasping, release, drawing, software). Explain the cause and fix plainly.
4. When the problem is visual, look at the saved photos in `runs/<profile>/` (`detect.jpg`,
   `<object>.jpg`, `*_after.jpg`). Use `so101 detect <colour>` to re-check what the camera sees.
5. Fix by changing the profile (`robot.yaml`) or recalibrating, never by loosening the success
   checks. After code changes run `python -m pytest -q`.
6. If the problem is new, add a row to `docs/TROUBLESHOOTING.md` (symptom, cause, fix).
