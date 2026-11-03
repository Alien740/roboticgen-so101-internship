---
name: so101-calibrate
description: Calibrate the SO-101 camera-to-robot mapping (table), the camera lens (checkerboard), or an object colour. Use when doctor reports "camera moved", grasps keep missing, the camera was remounted, a new object or colour is introduced, or the lighting changed.
---

# SO-101 calibration

Golden rules: AGENTS.md. Run `so101 doctor` first; motors and camera must be OK.

## Table (pixel to robot): the one that matters most
1. Ask the human to mount the camera looking down at the work area (15-33 cm in front of the base),
   have the orange cube ready, and keep the table and camera still for about 12 minutes.
2. `so101 agent start calibrate table`, then `so101 agent wait`.
3. Prompt "put the cube ON THE TABLE directly under the open jaws": relay it; answer `go` only after
   the human confirms. The same prompt can come back mid-run (cube lost): relay it again.
4. Keep calling `wait` until `ended`. In the `table_calibrated` event, `loo_median_mm` < 5 is good.
   `calib_point_not_visible` events mean those grid points are outside the camera view (fine if few).
5. If it aborts with "camera moved during calibration", the table was bumped: run it again.

## Lens (optional, needs a printed checkerboard)
Set `calibration.board` (inner corners, square size in mm) in the profile. Run
`so101 agent start calibrate camera`; the human holds and moves the board; 15 views are captured
automatically. Good: RMS < 0.5 px. Then redo the table calibration.

## Colour
With the object alone in the middle of the view: `so101 agent start calibrate hsv --color NAME`,
relay the prompt, then verify with `so101 detect NAME` and look at `runs/<profile>/detect.jpg`.
