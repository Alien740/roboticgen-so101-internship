# Procedures — what the person at the robot does

Conventions: robot base frame, **x = straight out from the base, y = to the robot's left**,
metres in the profile, mm in messages. The reachable work area is **15–33 cm from the base**.
Answer prompts in the terminal (or the AI relays them). `stop` / Ctrl+C halts motion at any time.

---

## 0. New PC

1. Clone the repo, `cd 08_so101_framework`.
2. Windows: `powershell -ExecutionPolicy Bypass -File install.ps1` · Linux/macOS: `bash install.sh`
3. Plug in the arm (USB **and** power supply) and the camera, then: `so101 doctor`
4. Fix every ERROR (the fix is printed under it). Warnings about intrinsics are optional.

Good: `result: ALL GOOD` or only the intrinsics warning.

## 1. Measure the robot's quirks — `so101 doctor --measure` (≈2 min)

Prepare: clear the area around the arm. Answer `go` to the confirmation.
The arm parks upright, sweeps the wrist roll both ways, opens/closes the empty gripper and makes
a small pan move for camera latency. Results → `profiles/<name>/measured.yaml`.
If it reports a different free wrist range than the profile, update `wrist.roll_min_ok/max_ok`.

## 2. Table calibration — `so101 calibrate table` (≈12 min)

Needed after the camera moves (doctor: "camera moved N px").
Prepare: mount the camera looking down at the table, the work area 15–33 cm out in view;
have the **orange cube** ready; keep the table and camera still for the whole run.

1. The arm parks, then hovers ~20 cm out with the gripper open.
2. Prompt "put the cube ON THE TABLE directly under the open jaws": place it (16 mm faces toward
   the pads), take your hand away, answer `go`.
3. It carries the cube to 21 points, photographing each. If it ever loses the cube it asks for
   another handover (same as step 2).
4. Result: "hold-one-out median X mm". Good < 5 mm. Images: `runs/<name>/table_calibration/`.

## 3. Camera lens calibration (optional) — `so101 calibrate camera`

Print a checkerboard (9×6 inner corners, measure the square size and set
`calibration.board` in the profile). Answer `go`, then hold the board in front of the camera and
move/tilt it slowly; 15 views are captured automatically. Good: RMS < 0.5 px. Then redo step 2.

## 4. Colours — `so101 calibrate hsv --color NAME` / `so101 detect NAME`

Put the object alone in the middle of the view, answer `go`. Thresholds go to `measured.yaml`.
`so101 detect orange yellow` prints what it finds (with robot coordinates) and saves
`runs/<name>/detect.jpg`.

## 5. Pick & place — `so101 pick-place --trials N`

Object (`tasks.pick_place.object`, default the cube) anywhere in reach; it is placed at
`tasks.pick_place.place_xy`. Between trials: move the object, answer `go`.
Logged: detection, grasp, success (within `success_tol_mm`), position error, cycle time.

## 6. Basket — `so101 basket --trials N [--objects cube]`

Put the basket (the 80×50×30 mm brick, hollow side up) 20–34 cm out and the object anywhere
15–30 cm out, ≥7 cm from the basket. Between trials: move the object, answer `go`.
Good on the reference arm: 6/6 reachable trials, ~22 s per cycle, ~15 mm from the basket centre.

## 7. Several objects — `so101 multi [--objects cube lego]`

All objects on the table, then run. Each is picked with its own profile settings (the lego is
gripped across its short side, lower and harder). Logged per object.

## 8. Stack the brick over the cube — `so101 stack --hover`, then `so101 stack`

Brick and cube ≥7 cm apart, both in reach. `--hover` shows the grasp point above the brick and
the release point above the cube and asks you to confirm each. Known limitation on the 5 V arm:
the brick grasp was unreliable.

## 9. Drawing — `so101 draw`

Prepare: paper taped down covering ~22–34 cm in front of the base; a felt-tip, cap off.

1. **Load**: gripper opens (unrotated). Lay the pen **across the jaws like a cigarette**, deep
   between the pads, tip out 5–7 cm. `go`. It clamps, then backs off to a gentle hold. Tug-test, `go`.
2. **Tilt**: the wrist tilts so the pen should point straight down over the paper.
   Look at it: `go` if the TIP points straight down · `roll 10` / `roll -10` to fine-tune ·
   `flip` if the cap end is down · `nudge <away_mm> <left_mm>` to move the spot.
   The confirmed direction is saved for next time.
3. **Height**: tell it how high the tip is (mm, a ruler or a good guess, err low).
   It moves to ~2 cm above the paper. Jog: `down 5`, `down 3`, `down` … `touch` when it just meets the paper.
4. **Level** (recommended): `level 60` — it touches 3 corners of a 60 mm area (jog each to `touch`).
5. **Draw**: `draw circle 60`, `draw star 50`, `draw smiley 60`, `draw house 60`, `draw square 40`, `draw spiral 50`.
   Skipping lines → `press 3.5`; wobbly → `speed 8`; clean spot → `move 0 60` then `probe`.
6. `quit` → it brings the pen back; hold it, `go`, it releases and folds home.

Tip: if the gripper overloads during long sessions, tape the pen to the fixed jaw.

## 10. Metrics — `so101 report`

Writes `runs/<name>/report.md`: per task detection / grasp / success rates, position error,
cycle time and the ranked failure reasons.
