# AGENTS.md — SO-101 dashboard (for any AI assistant)

This project is a web UI on top of `../08_so101_framework`. Read that folder's `AGENTS.md` first:
its golden rules (the arm is real, the person confirms physical facts, run `so101 doctor` after
errors, change settings in the profile not in code) apply here unchanged.

Dashboard-specific rules:

1. Start the server in the background: `so101-dashboard` (`--sim` without hardware). It runs until stopped.
2. Never press motion controls (poses, sliders, jog, keyboard, gamepad) or call the motion API
   (`/api/goto`, `/api/set`, `/api/jog`, `/api/torque`) without the person's explicit go-ahead.
   `/api/estop` is always allowed.
3. The dashboard holds the serial port while connected: framework commands (`so101 doctor`, tasks)
   need the person to press **Park & disconnect** first.
4. All arm motion goes through `so101_dashboard/controller.py`. Keep every new feature on that
   path (never talk to the backend from an HTTP handler) so E-STOP, limits and the stall guard
   keep applying.
5. After code changes run `python -m pytest -q` here (simulated arm + HTTP API, no hardware).
6. New symptoms go into `docs/TROUBLESHOOTING.md` (symptom, cause, fix).
