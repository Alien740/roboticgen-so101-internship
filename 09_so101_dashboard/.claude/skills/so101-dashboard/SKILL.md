---
name: so101-dashboard
description: Start and support the SO-101 web dashboard (3D twin, joint sliders, tool jog, keyboard/gamepad control, motor status). Use when the user wants to drive the arm by hand, see it in 3D, check motor temperatures/voltages, or demo the robot without writing commands.
---

# Dashboard

1. Start it in the background (it keeps running): `so101-dashboard` (run from `09_so101_dashboard`, add `--sim` to try it
   without hardware, `--lan` to open it from a phone; `--lan` prints a secret link — only share it
   with people in the room). It opens http://127.0.0.1:8000.
2. The person chooses **Connect real arm** or **Simulated arm** in the page. Connecting does not
   move the arm; pose buttons, sliders, jog, keyboard and gamepad do.
3. Never press motion controls on the person's behalf without their go-ahead. E-STOP (Space/Esc,
   gamepad B) is always safe to press.
4. Motions tab: hello / scare / dance + recordings; Preview is safe (ghost on the twin only), Play
   moves the arm. Record tab: "Move it by hand" switches torque off - the person must hold the arm.
   Recordings are saved in `09_so101_dashboard/motions/`.
5. `so101 doctor` and every task need the serial port: ask the person to press **Park & disconnect**
   (or stop the server) first. The dashboard's Status tab has its own Run doctor button.
6. Problems: see `docs/TROUBLESHOOTING.md` in `09_so101_dashboard`. The server prints
   tracebacks to its console; warnings also appear under the 3D twin.
7. After code changes: `python -m pytest -q` in `09_so101_dashboard` (tests/test_dashboard.py drives the simulated arm
   and the HTTP API).
