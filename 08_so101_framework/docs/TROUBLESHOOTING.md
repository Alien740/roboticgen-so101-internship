# Troubleshooting: symptom → cause → fix

Every entry below happened on the reference SO-101 (5 V kit, Windows 11, overhead USB webcam)
while this framework was being built. Most fixes are already built into the code; the table
says what to do when you still see the symptom. **First step for any problem: `so101 doctor`.**

## Connection & power

| Symptom | Cause | Fix |
|---|---|---|
| `doctor`: "ports: []" | Arm USB cable unplugged, or no CH343/CH340 driver | Plug in USB; install the driver; on Linux add yourself to `dialout` |
| "no port answers with 6 motors" / lerobot "Missing motor IDs" (all) | Arm **power** supply off — USB alone powers the board, not the motors | Plug in / switch on the barrel-jack supply |
| "Missing motor IDs: 6" right after a restart | Gripper briefly unresponsive at handshake (just released a clamp) | Connect retries automatically; if it persists, power-cycle |
| `Failed to write 'Lock' … no status packet` | Occasional dropped packet during `configure()` | Retried automatically (3×) |
| "Port is in use" / "Access is denied" mid-run | USB dropout (loose cable, PC USB power-saving) or another program holds COM | Re-seat cable, plug directly into the PC (no hub), close other serial tools; disable USB selective suspend |
| Motors weak, gripper slow, voltage ≈ 5 V | 5 V kit: STS3215s are underpowered | Expected; keep the gripper settings in the profile (torque 450, cap lifted) |

## Motor faults (status register)

| Symptom | Cause | Fix |
|---|---|---|
| `Overload error!` on gripper writes; gripper ignores commands | Gripper held at goal 0 (= permanent full-force stall) for minutes, e.g. clamping a pen | **Unplug arm power 10 s.** Prevention: `Arm.hold()` backs the goal off to just past contact; idle torque 400 |
| Gripper overload during long drawing sessions despite `hold()` | 5 V gripper can't sustain any clamp for many minutes | Tape / rubber-band the pen to the fixed jaw; keep sessions short; `quit` when idle |
| Overheat flag | Sustained load | Let it cool 10 min |

## Camera

| Symptom | Cause | Fix |
|---|---|---|
| Frames are black | Wrong index (Windows re-numbers cameras on replug) or laptop camera shutter | `camera.index: auto` (default); `doctor` shows which index is live |
| A logo / test card instead of the table | OBS virtual camera picked | Auto-selection rejects static (pixel-identical) streams |
| Photos show the arm where it was seconds ago | DSHOW queues frames; on-demand reads were stale | Built in: reader thread + "frame newer than request" |
| Identical frames forever | Driver froze | Built in: detected after 2 s and the device is reopened |
| "camera moved N px since calibration" | Camera bumped (a table shake moved it 38 px) | Put it back, or `so101 calibrate table` |
| Calibration aborts "camera moved during calibration" | Table bumped mid-run | Don't touch the table during the 12-minute run; re-run |
| `so101 detect` finds nothing | Camera not looking at the table / object out of view / colour thresholds off | Check `runs/<profile>/detect.jpg`; `so101 calibrate hsv --color X` |

## Detection

| Symptom | Cause | Fix |
|---|---|---|
| Orange cube missed in warm light | HSV hue drifted; wood reached the same saturation | Lab a* thresholds (default) |
| Skin detected as the cube | Skin a* up to ~144 overlaps the cube's edge pixels | Hysteresis: blob must contain a core pixel a* ≥ 148 |
| Small cube rejected | Pixelated edges: fill ≈ 0.80, roundness 0.82 | Fill ≥ 0.70, roundness ≤ 0.88 (discs ≈ 1.0) |
| Brick / lego not detected | Aspect 1.64 / 2+ exceeded the cube's 1.6 limit | Per-object `max_aspect` in the profile |
| The wall detected as "paper" | White wall passes the colour test | Paper candidates must lie inside the arm's reach |
| Yellow lego confused with the yellow basket | Same colour | Basket = largest blob; detections inside its hull are ignored |
| Cube at the rim counted as "not in basket" | Cube notches the basket outline | Convex hull for the inside test |

## Calibration

| Symptom | Cause | Fix |
|---|---|---|
| Hand-guided calibration impossible | Needs a person to back-drive the arm | Use `calibrate table`: the arm places the cube itself |
| Cube dropped 2 cm and bounced at release | Cube handed INTO the jaws: unknown offset | Cube is placed on the table and grasped from there |
| Re-grasp "missed" though the cube was held | Soft cube squeezed: position looked "empty" | Thresholds from measured empty-close (empty ≈ 0.4) |
| 3 points share one pixel | Stale camera frames | Fixed (fresh frames) |
| Hold-one-out error ≫ 5 mm | Calibration made under different light / poses / camera position | Re-run `calibrate table`; it uses the same motion primitives as the tasks |
| One point 12 mm off | Cube shifted when released | Worst point dropped automatically if > 2.5× the median |

## Grasping

| Symptom | Cause | Fix |
|---|---|---|
| Jaw lands ON the object (gripper stalls ~46–55) | Calibrated grasp point has the FIXED jaw touching the object | Shift toward the fixed jaw: half-width + clearance − 8 mm (built in) |
| Gripper stops half-open, never grips (pos ~45–55) | 8-unit per-step cap starved the motor; torque 300 too weak | Cap lifted for gripper moves; torque 450 |
| Soft cube crushed / reads "empty" | Torque 500+ | Torque 450 for the cube; per-object torque |
| Real grip rejected as "jam" (pos ~28) | Cube gripped across its 19 mm side reads higher | Jam threshold 40 |
| Lego slipped out on lift | Pinched by its corners; too little contact; too weak | Wrist aligned across the short side; grasp z 4 mm; torque 800 |
| Jaws shake on the way down | Each waypoint solved from scratch → branch changes (23° jumps) | Single-seed chained IK; jump guard |
| Moving jaw hits the table | Gripper tilted toward the moving jaw at the end of the descent | End pose solved preferring vertical; pitch blended along the line |
| Brick knocked over on descent | Joint-space descent swings 7 mm sideways | Straight-line descent (`move_linear`) |
| "IK failed" near the base | No top-down solution at travel height below ~15 cm | `reach_min 0.15`; objects closer are refused before moving |

## Release

| Symptom | Cause | Fix |
|---|---|---|
| Cube flew 14 cm past the basket | Squeezed foam springs out when the jaws snap open | Release low (rim + 15 mm), open slowly and only to 45 |
| "Success" but the object was on the table | Check only tested "moved ≥ 4 cm" | Object must be gone from the table outside the basket |

## Drawing

| Symptom | Cause | Fix |
|---|---|---|
| Pen points sideways when "down" | URDF wrist-roll zero is ~90° off this arm | Pen-down = gripper unrotated + wrist tilted; operator confirms by eye, direction learned |
| Wrist only reaches 80°, strains at full load | Mechanical jam near +80° roll | Free range −100…+75 (measured by `doctor --measure`) |
| Automatic touch pressed the pen hard, drew a stray line | Load-based contact detection fails in the pen pose | Manual jog + "touch" |
| Lines skip on one side | Paper height measured at one point; it varied 5.5 mm over 6 cm | `level 60`: touch 3 corners |
| Shivering lines | 2 mm waypoints held 0.17 s each at slow speed | 30 Hz streaming of ~0.4 mm steps |
| Pen sits on the paper bleeding a dot | Path planned after the pen went down | Planned first |
| Shapes slightly distorted | Pen leans up to ~15° (wrist_flex at its stop); tool point ≠ tip | Tip is aimed (pen length from the touch-off) |
| "path has a 13 deg joint jump" on the descent | Straight line crosses a region near the wrist limit | Long descents are joint-space moves to 2 cm above the paper |

## Software

| Symptom | Cause | Fix |
|---|---|---|
| IK "unreachable" for points clearly in reach | Cost in m²: 1 mm error = 1e-6, below L-BFGS tolerance | Costs in mm² (built in) |
| IK returns poses 3 mm off | Candidate chosen on pitch only | Combined score: 1 mm ≈ 3° |
| `torch` DLL load error once | Transient Windows DLL init failure | Re-run the command |
| `cv2.imshow` does nothing / errors | `opencv-python-headless` (what lerobot installs) has no GUI | By design: previews are saved as images in `runs/` |
