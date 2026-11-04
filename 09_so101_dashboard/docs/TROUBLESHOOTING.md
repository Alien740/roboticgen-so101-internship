# Dashboard troubleshooting: symptom → cause → fix

Robot, camera and task problems are covered in
[`../08_so101_framework/docs/TROUBLESHOOTING.md`](../08_so101_framework/docs/TROUBLESHOOTING.md).

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'so101'` | The framework isn't installed in this Python | Run `08_so101_framework/install.ps1` (or `install.sh`) first, then this folder's installer |
| "Connect real arm" → `connect failed: no port with all 6 motors` | Arm power off, or another program has the port | Power the arm; close other scripts/`so101` sessions; Status tab → Run doctor |
| Run doctor says "disconnect the arm first" | Doctor needs the serial port the dashboard is using | Park & disconnect, then run doctor |
| Tool jog stops with "… is at its limit" | The straight line needs a joint past its limit (e.g. shoulder at −95° in clear view) | Jog another direction, or use the joint sliders to change the arm's shape first |
| Tool jog stops with "edge of reach" / "at the table" | It refuses to leave the workspace or go below the table | Expected; move back toward the robot / up |
| Keyboard or gamepad does nothing | Their checkboxes are off by default, the page isn't focused, or (gamepad) no button pressed yet | Tick the checkbox, click the page, press any gamepad button once |
| Gamepad axes feel wrong | The browser didn't apply the standard mapping | Use an Xbox-style pad in Chrome/Edge; switch joint/tool mode with Back |
| "gripper is holding something … eased off" | Stall guard: the jaw stopped on an object | Expected; the grip is kept just past contact to avoid an overload trip |
| 3D twin missing | No WebGL2, or `static/twin_mesh.json` missing | Use Chrome/Edge/Firefox; rebuild with `python -m so101_dashboard.mesh <stl folder>` |
| Port 8000 already in use | Another dashboard or app is running | Close it, or `so101-dashboard --port 8001` |
| Phone can't open the `--lan` link | Firewall blocks the port, or the phone is on another network | Allow Python through Windows Firewall (private networks); same Wi-Fi |
