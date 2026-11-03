"""`so101 stack [--hover]`: pick the hollow brick and drop it over the cube.

--hover: no grasp - shows the grasp point above the brick and the release point above the
cube, asking the operator to confirm each (do this first on a new setup).

The held brick sits against the FIXED jaw, so its centre is (half-width - cube half) from the
calibrated grasp point: the release point is shifted by that much toward the fixed jaw.
Success = the cube is hidden and the brick is within success_tol_mm of where the cube was.
Known limitation (documented): grasps of the brick were unreliable on this 5 V arm.
"""
from __future__ import annotations

import time

import numpy as np

from .. import kinematics as K
from .common import CUBE_HALF_ACROSS, Scene, grasp, log_row, new_row, reachable, travel_z


def main(a, prof, sess):
    from ..hardware.arm import Arm
    t = prof["tasks"]["stack"]
    bname, cname = t["object"], t["target"]
    brick, cube = prof["objects"][bname], prof["objects"][cname]
    release_z = cube["height"] + t["drop_clear"] + brick["grasp_z"]
    scene = Scene(prof, sess)
    try:
        with Arm(prof) as arm:
            row = new_row("stack", bname)
            arm.park()
            t0 = time.time()
            fb, fc = scene.find(bname, exclude_basket=False), scene.find(cname, exclude_basket=False)
            scene.check_still(fb["frame"])
            if fb["xy"] is None or fc["xy"] is None:
                row["failure"] = "not detected: " + ", ".join(n for n, f in ((bname, fb), (cname, fc)) if f["xy"] is None)
                return log_row(prof, sess, row)
            row.update(detected=1, x_mm=round(fb["xy"][0] * 1000, 1), y_mm=round(fb["xy"][1] * 1000, 1))
            tz = travel_z(prof)
            for label, p, hs in ((bname, fb["xy"], (tz, brick["grasp_z"])), (cname, fc["xy"], (tz, release_z))):
                why = reachable(prof, p, hs)
                if why:
                    row["failure"] = f"{label} out of reach: {why}"; return log_row(prof, sess, row)
            if np.hypot(*(fb["xy"] - fc["xy"])) < 0.07:
                row["failure"] = "brick and cube closer than 7 cm - the open jaws could hit the cube"
                return log_row(prof, sess, row)

            if a.hover:
                from .common import half_across, short_axis_deg
                w = prof["wrist"]
                a0 = K.ik((*fb["xy"], brick["grasp_z"]), {"shoulder_pan": 0, "shoulder_lift": 0, "elbow_flex": 0, "wrist_flex": 80})[0]
                roll = K.roll_for_axis(a0, short_axis_deg(fb["box"]), 180.0, (w["roll_min_ok"], w["roll_max_ok"]))
                jdir = K.jaw_dir(K.ik((*fb["xy"], brick["grasp_z"]), a0, roll=roll)[0])
                h = half_across(fb["box"], brick["size"], jdir)
                gx = fb["xy"] + jdir * (h + brick.get("clearance", 0.008) - CUBE_HALF_ACROSS)
                arm.open()
                cur = arm.angles()
                arm.move_joints({f"{k}.pos": v for k, v in K.ik((*gx, tz), cur, roll=roll)[0].items() if k != "gripper"})
                arm.move_linear((*gx, brick["height"] + 0.02), duration=1.5)
                sess.ask("jaws 2 cm above the brick: both jaws clear of its top, one outside each long wall? 'go'")
                arm.move_linear((*gx, tz), duration=1.2)
                rx = fc["xy"] + jdir * (h - CUBE_HALF_ACROSS)
                arm.move_xyz((*rx, tz))
                arm.move_linear((*rx, release_z + 0.02), duration=1.5)
                sess.ask("over the cube: would a held brick be centred over it? 'go'")
                arm.move_linear((*rx, tz), duration=1.2)
                arm.park()
                return

            ok, _, h = grasp(arm, scene, bname, fb, row)
            if ok:
                row["grasped"] = 1
                cur = arm.angles()
                jdir = K.jaw_dir(K.ik((*fc["xy"], release_z), cur, roll=cur["wrist_roll"])[0])
                rx = fc["xy"] + jdir * (h - CUBE_HALF_ACROSS)
                pos = arm.tool_pose()[0]
                arm.move_linear((pos[0], pos[1], tz), duration=1.8)
                arm.move_xyz((*rx, tz))
                arm.move_linear((*rx, release_z), duration=2.0)
                arm.open()
                arm.move_linear((*rx, tz), duration=1.5)
            arm.park()
            row["cycle_time_s"] = round(time.time() - t0, 1)
            if row["grasped"]:
                after_b, after_c = scene.find(bname, exclude_basket=False, tag="stack_after"), scene.find(cname, exclude_basket=False)
                if after_b["xy"] is None:
                    row["failure"] = "brick not visible after the drop"
                else:
                    err = float(np.hypot(*(after_b["xy"] - fc["xy"])) * 1000)
                    row["error_mm"] = round(err, 1)
                    row["success"] = int(err < t["success_tol_mm"] and after_c["xy"] is None)
                    if not row["success"]:
                        row["failure"] = ("cube still visible " if after_c["xy"] is not None else "") + \
                                         (f"brick {err:.0f} mm off" if err >= t["success_tol_mm"] else "")
            log_row(prof, sess, row)
    finally:
        scene.close()
