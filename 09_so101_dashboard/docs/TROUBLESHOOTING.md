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
| Hand tab: "Camera / model failed: Permission denied" | The browser (or macOS) has not allowed the camera | Click the camera icon in the address bar → Allow; macOS: System Settings → Privacy & Security → Camera → enable your browser. Embedded preview panes cannot use the camera — open the page in Chrome/Safari |
| Hand tab: "Camera / model failed: Failed to fetch" | The hand model is downloaded from jsdelivr / Google and there is no internet | Connect once to load it (the browser caches it) |
| Hand tab: "hand is outside the arm's reach – holding the last pose" | The mapped target is beyond what the IK can reach with that pitch | Move the hand back toward neutral, lower the Reach/Height range sliders, or press Set neutral |
| Hand tab: arm moves the wrong way left/right | You face the robot instead of standing behind it | Tick "I face the robot (mirror left/right)" |
| Hand tab: jittery arm | Webcam noise | Raise Smoothing; use Slow speed; better light on your hand |
