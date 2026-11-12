# 09 · SO-101 dashboard

A web dashboard for the SO-101 arm: live 3D twin, manual / keyboard / gamepad control and motor
status in one page. It runs on top of [`08_so101_framework`](../08_so101_framework) (same robot
profile, calibration, kinematics and safety lessons) and adds no dependencies of its own: Python
standard library on the server, plain JavaScript + WebGL in the browser, works offline.

## Install

The framework must be installed first (it creates the `.venv` this project uses):

```powershell
cd 08_so101_framework
powershell -ExecutionPolicy Bypass -File install.ps1      # Linux/macOS: bash install.sh
cd ..\09_so101_dashboard
powershell -ExecutionPolicy Bypass -File install.ps1      # Linux/macOS: bash install.sh
```

## Run

```powershell
..\08_so101_framework\.venv\Scripts\Activate.ps1          # Linux/macOS: source ../08_so101_framework/.venv/bin/activate
so101-dashboard            # opens http://127.0.0.1:8000 ; press "Connect real arm" or "Simulated arm"
so101-dashboard --sim      # start with the simulated arm (no hardware needed)
so101-dashboard --lan      # also reachable from a phone/tablet on the same Wi-Fi (prints a secret link)
```

Options: `--profile NAME` (framework profile, default `my_so101`), `--port 8000`, `--no-browser`.
`python -m so101_dashboard` works too.

## What's in it

- **3D twin** of the real arm (URDF meshes, decimated to 28k triangles) mirroring the measured
  joints; orbit, zoom, front/side/top views
- **Manual**: profile poses, joint sliders (the arm follows at the chosen speed, a small tick shows
  the real position), straight-line tool jog (X/Y/Z, pitch kept), gripper open/close, speed
  presets Slow/Normal/Fast, torque off for moving the arm by hand
- **Keyboard**: `Q/E W/S A/D Z/X R/F T/G` joints, arrows + PgUp/PgDn tool, Shift boost, 1/2/3 speed
- **Gamepad**: same layout as the old `gamepad_control.py` (sticks pan/lift and roll/flex, LB/RB
  elbow, triggers gripper, Y boost, A home), Back switches to tool mode
- **Status**: per-motor position, goal, temperature, voltage, load, torque and fault flags; OS,
  Python, lerobot and serial ports; **Run doctor** button
- **Motions**: built-in **hello** (raises its hand and waves), **scare** (hides low, then jumps up
  snapping its jaws) and **dance** (sways, bobs and claps to a beat), plus your recordings; play at
  0.5×–2×, **Preview** plays a see-through ghost on the 3D twin without moving the arm, Stop any time
- **Record**: record the arm moved **by hand** (torque off; hold it first) or driven with sliders,
  keyboard or gamepad; still start/end trimmed; saved to [`motions/`](motions/) as JSON (commit the
  good ones), rename / delete in the Motions tab

## Safety

- **E-STOP** on every screen (Space / Esc / gamepad B) freezes the arm where it is and latches until Reset
- keyboard and gamepad are off until their boxes are ticked; jog input expires after 0.3 s
  (lost tab, unplugged gamepad, stuck key → the arm stops)
- wrist roll limited to the profile's measured free range (it jams near +80°)
- a stalled grip is eased to just past contact before the servo's overload protection trips
- tool jog refuses to go below the table, beyond reach or past a joint limit, and says why
- disconnect parks at `home` before torque is released; torque-off asks you to hold the arm first
- motions are checked before they play: joint limits, no joint faster than 300°/s at the chosen
  speed (scare at 2× is refused), built-ins keep the whole arm ≥ 4 cm above the table; they start
  with a move to their first pose at the speed preset; manual input, Stop or E-STOP cancels them
- other web pages cannot drive the arm (custom header required); `--lan` also requires a secret token

## How it works

| File | What |
|---|---|
| `so101_dashboard/controller.py` | the only owner of the arm: a 50 Hz loop; inputs only change targets; speed presets, limits, E-STOP, stall guard |
| `so101_dashboard/backends.py` | real arm (via the framework's `Arm`) or simulated arm, same interface |
| `so101_dashboard/server.py` | HTTP API + Server-Sent Events (live state ~20×/s), standard library only |
| `so101_dashboard/motions.py` | built-in motions, recordings → motions, safety check, the `motions/` library |
| `so101_dashboard/frames.py` | pose of every URDF link for the twin |
| `so101_dashboard/mesh.py` | builds `static/twin_mesh.json` from the STL parts (already built and committed) |
| `so101_dashboard/static/` | the page: `index.html`, `app.js` (UI, keyboard, gamepad), `motions.js` (Motions + Record tabs), `twin.js` (WebGL, ghost preview) |
| `motions/` | your saved recordings (JSON) |

## Roadmap

1. ✅ Core: simulated arm, status, E-STOP, 3D twin, manual / keyboard / gamepad control
2. ✅ Motions (hello, scare, dance) and record / replay
3. Camera, calibration and the framework's tasks
4. Drawing board
5. AI commands (Claude, OpenAI or a local model)

## Tests (no hardware)

```
python -m pytest -q
```

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) when something goes wrong.
