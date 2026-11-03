"""`so101 pick-place [--trials N]`: find the object anywhere, place it at the fixed destination.

Metrics per trial (runs/<profile>/pick_place_trials.csv): detection, grasp, success
(object seen within success_tol_mm of the destination), position error, cycle time, failure.
"""
from __future__ import annotations

import time

import numpy as np

from .common import Scene, carry_and_release, grasp, log_row, new_row, reachable, travel_z


def one_trial(arm, scene, prof, sess):
    t = prof["tasks"]["pick_place"]
    name, dest = t["object"], np.array(t["place_xy"])
    row = new_row("pick_place", name)
    arm.park()
    t0 = time.time()
    f = scene.find(name)
    try:
        scene.check_still(f["frame"])
    except RuntimeError as e:
        row["failure"] = str(e); return row
    if f["xy"] is None:
        row["failure"] = "not detected"; return row
    xy = f["xy"]
    row.update(detected=1, x_mm=round(xy[0] * 1000, 1), y_mm=round(xy[1] * 1000, 1))
    obj = prof["objects"][name]
    for label, p, hs in ((name, xy, (travel_z(prof), obj["grasp_z"])), ("destination", dest, (travel_z(prof), t["place_z"]))):
        why = reachable(prof, p, hs)
        if why:
            row["failure"] = f"{label} out of reach: {why}"; return row
    ok, _, _ = grasp(arm, scene, name, f, row)
    if ok:
        row["grasped"] = 1
        if not carry_and_release(arm, dest, t["place_z"], slow=False):
            row["failure"] = "dropped in transit"
    arm.park()
    row["cycle_time_s"] = round(time.time() - t0, 1)
    if row["grasped"] and not row["failure"]:
        g = scene.find(name, exclude_basket=False, tag="pick_place_after")
        if g["xy"] is None:
            row["failure"] = "object not seen after placing"
        else:
            err = float(np.hypot(*(g["xy"] - dest)) * 1000)
            row["error_mm"] = round(err, 1)
            row["success"] = int(err <= t["success_tol_mm"])
            if not row["success"]:
                row["failure"] = f"placed {err:.0f} mm from the destination"
    return row


def main(a, prof, sess):
    from ..hardware.arm import Arm
    scene = Scene(prof, sess)
    try:
        with Arm(prof) as arm:
            for i in range(a.trials):
                if i:
                    sess.ask("move the object to a new spot, then 'go'", ["go", "stop"])
                log_row(prof, sess, one_trial(arm, scene, prof, sess))
    finally:
        scene.close()
