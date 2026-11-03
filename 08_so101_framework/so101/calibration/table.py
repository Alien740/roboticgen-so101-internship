"""`so101 calibrate table [--grid main|offset|all]`: robot-driven pixel -> base calibration.

The operator places the cube once under the open jaws. The arm then carries it to every grid
point, sets it down, parks out of view, and the camera finds it: (pixel, FK position) pairs.
A homography is fitted on the undistorted pixels; accuracy = hold-one-out error.

Lessons encoded here:
- Grasp from the TABLE (not handed into the jaws): every grasp then has the same offset, and the
  release 2 mm above grasp height drops the cube ~2 mm instead of ~2 cm.
- Calibrate with the same motion primitives the tasks use (joint move to travel height, straight
  descent) so the arm reaches identical poses at calibration and pick time.
- A table shake moved the camera mid-run: the first frame is the reference and every later frame
  is checked against it.
- A rotated cube is sometimes missed on re-grasp: ask for a new handover instead of losing the
  points collected so far.
"""
from __future__ import annotations

import json

import cv2
import numpy as np

from ..hardware.camera import Camera, camera_shift
from ..vision.detect import detect, draw
from ..vision.mapping import fit_direct, load_intrinsics, undistort_points


def grid_points(cfg, name):
    g = cfg["grids"]
    if name in g:
        return [(x, y) for x in g[name]["x"] for y in g[name]["y"]]
    pts = [p for k in g for p in grid_points(cfg, k)]
    xs = sorted({p[0] for p in pts})
    return [p for i, x in enumerate(xs) for p in sorted([q for q in pts if q[0] == x], key=lambda q: q[1], reverse=bool(i % 2))]


def main(a, prof, sess):
    from ..hardware.arm import Arm
    cfg, m = prof["tasks"]["table_calibration"], prof["motion"]
    obj = prof["objects"][cfg["object"]]
    grid = grid_points(cfg, a.grid)
    handover = tuple(cfg["handover_xy"])
    K, dist = load_intrinsics(prof)
    out_dir = prof.run_dir / "table_calibration"
    out_dir.mkdir(exist_ok=True)
    pxs, base, ref = [], [], None
    sess.say(f"table calibration: {len(grid)} points with the {cfg['object']} (~{len(grid) * 35 // 60} min). "
             "Keep the table and camera still.")

    def median_px(cam):
        pts, last = [], (None, [])
        for _ in range(5):
            f = cam.read()
            d = detect(f, prof, obj["color"], obj.get("max_aspect", 1.6))
            last = (f, d)
            if d:
                pts.append(d[0].center_px)
        return (np.median(pts, axis=0) if len(pts) >= 3 else None), last

    def handover_grasp(arm):
        arm.move_xyz((*handover, m["approach_z"]), duration=3.0)
        arm.open()
        sess.ask(f"put the {cfg['object']} ON THE TABLE directly under the open jaws, move your hand away, then 'go'")
        arm.move_linear((*handover, m["grasp_z"]), duration=1.8)
        pos = arm.close()
        sess.event("grasp_result", gripper=round(pos, 1), state=arm.holding(pos))
        return np.array([*handover, m["grasp_z"]]), arm.holding(pos) == "held"

    with Camera.from_profile(prof) as cam, Arm(prof) as arm:
        arm.park()
        xyz, ok = handover_grasp(arm)
        if not ok:
            arm.open(); arm.move_linear((*handover, m["travel_z"]), duration=1.5)
            raise SystemExit("the cube is not in the gripper - check it is under the jaws and retry")
        arm.move_linear((*handover, m["travel_z"]), duration=1.5)
        for i, (x, y) in enumerate(grid):
            sess.check_stop()
            sess.event("calib_point", index=i + 1, of=len(grid), target_mm=[round(x * 1000), round(y * 1000)])
            try:
                arm.move_xyz((x, y, m["travel_z"]))
                arm.move_linear((x, y, m["grasp_z"] + cfg["release_dz"]), duration=1.8)
            except RuntimeError as e:
                sess.event("calib_skip", reason=str(e)); continue
            arm.open()
            xyz = arm.tool_pose()[0]
            arm.move_linear((xyz[0], xyz[1], m["travel_z"]), duration=1.5)
            arm.park()
            px, (frame, dets) = median_px(cam)
            if ref is None:
                ref = frame.copy()
            elif (s := camera_shift(ref, frame)) > prof["camera"].get("max_shift_px", 3.0):
                raise SystemExit(f"camera moved {s:.1f} px during calibration - fix it and run again")
            cv2.imwrite(str(out_dir / f"pt{i + 1:02d}.jpg"), draw(frame, dets))
            if px is None:
                sess.event("calib_point_not_visible", index=i + 1)
            else:
                pxs.append([float(v) for v in px]); base.append([float(v) for v in xyz[:2]])
                sess.event("calib_point_ok", index=i + 1, px=[round(v, 1) for v in px])
            arm.move_xyz((xyz[0], xyz[1], m["travel_z"]))
            arm.move_linear((xyz[0], xyz[1], m["grasp_z"]), duration=1.8)
            pos = arm.close()
            if arm.holding(pos) != "held":
                sess.event("regrasp_failed", gripper=round(pos, 1))
                arm.open(); arm.move_linear((xyz[0], xyz[1], m["travel_z"]), duration=1.5)
                xyz, ok = handover_grasp(arm)
                if not ok:
                    sess.say("handover grasp failed - stopping the grid early"); break
            arm.move_linear((xyz[0], xyz[1], m["travel_z"]), duration=1.5)
        # set the cube down and finish
        arm.move_linear((xyz[0], xyz[1], m["grasp_z"] + cfg["release_dz"]), duration=1.8)
        arm.open()
        arm.move_linear((xyz[0], xyz[1], m["travel_z"]), duration=1.5)

    if len(pxs) < 6:
        raise SystemExit(f"only {len(pxs)} usable points - need >= 6 (is the work area in the camera view?)")
    H, res, loo = fit_direct(undistort_points(pxs, K, dist), base)
    if len(loo) >= 8:      # drop the single worst point if it is a clear outlier (cube shifted on release)
        worst = int(np.argmax(loo))
        if loo[worst] > 2.5 * np.median(loo):
            keep = [i for i in range(len(pxs)) if i != worst]
            sess.event("outlier_dropped", px=pxs[worst], loo_mm=round(loo[worst], 1))
            pxs, base = [pxs[i] for i in keep], [base[i] for i in keep]
            H, res, loo = fit_direct(undistort_points(pxs, K, dist), base)
    tf = prof.path(prof["calibration"]["table_file"])
    tf.write_text(json.dumps({"mode": "direct-auto", "H_px_to_table_mm": H.tolist(),
                              "T_table_mm_to_base_m": [[1e-3, 0, 0], [0, 1e-3, 0]],
                              "points_px": pxs, "points_base_m": base,
                              "residuals_mm": res.tolist(), "loo_mm": loo}, indent=2))
    cv2.imwrite(str(prof.path(prof["calibration"]["reference_image"])), ref)
    med = float(np.median(loo)) if loo else float("nan")
    sess.event("table_calibrated", points=len(pxs), loo_median_mm=round(med, 1), loo_max_mm=round(max(loo), 1) if loo else None)
    sess.say(f"saved {tf.name}: {len(pxs)} points, hold-one-out median {med:.1f} mm "
             f"({'good' if med < 5 else 'POOR - check lighting / camera mount and re-run'})")
