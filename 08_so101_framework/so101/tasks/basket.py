"""`so101 basket [--trials N] [--objects NAME...]`: pick objects anywhere, drop them in the basket.

Success = the object is no longer anywhere on the table outside the basket (camera check).
Drop error = distance from the basket centre when the object is visible inside it.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from ..vision.detect import detect
from .common import Scene, carry_and_release, grasp, log_row, new_row, reachable, travel_z


def drop_one(arm, scene, prof, sess, name, task="basket"):
    row = new_row(task, name)
    obj, b = prof["objects"][name], prof["basket"]
    arm.park()
    t0 = time.time()
    f = scene.find(name)
    try:
        scene.check_still(f["frame"])
    except RuntimeError as e:
        row["failure"] = str(e); return row
    if f["xy"] is None or f["basket"] is None:
        row["failure"] = "not detected: " + ", ".join(n for n, v in ((name, f["xy"]), ("basket", f["basket"])) if v is None)
        return row
    xy, basket = f["xy"], f["basket"]
    row.update(detected=1, x_mm=round(xy[0] * 1000, 1), y_mm=round(xy[1] * 1000, 1))
    rz = b["height"] + b["rim_clear"]
    for label, p, hs, rmax in ((name, xy, (travel_z(prof), obj["grasp_z"]), None),
                               ("basket", basket, (travel_z(prof), rz), prof["motion"]["drop_reach_max"])):
        why = reachable(prof, p, hs, rmax)
        if why:
            row["failure"] = f"{label} out of reach: {why}"; return row
    ok, _, _ = grasp(arm, scene, name, f, row)
    if ok:
        row["grasped"] = 1
        if not carry_and_release(arm, basket, rz, slow=True):
            row["failure"] = "dropped in transit"
    arm.park()
    row["cycle_time_s"] = round(time.time() - t0, 1)
    if row["grasped"] and not row["failure"]:
        g = scene.find(name, tag=f"{task}_after")
        if g["xy"] is not None:
            row["failure"] = f"{name} still on the table at ({g['xy'][0]*1000:.0f}, {g['xy'][1]*1000:.0f}) mm"
        else:
            row["success"] = 1
            hull = g["hull"]
            inside = [d for d in detect(g["frame"], prof, obj["color"], obj.get("max_aspect", 1.6))
                      if hull is not None and cv2.pointPolygonTest(hull, d.center_px, False) >= 0
                      and d.area < 0.5 * cv2.contourArea(hull)]
            if inside:
                p = scene.mapper.base_xy([inside[0].center_px])[0]
                row["error_mm"] = round(float(np.hypot(*(p - basket)) * 1000), 1)
    return row


def main(a, prof, sess):
    from ..hardware.arm import Arm
    names = a.objects or [prof["tasks"]["basket"]["object"]]
    scene = Scene(prof, sess)
    try:
        with Arm(prof) as arm:
            for i in range(a.trials):
                if i:
                    sess.ask("move the object(s) to new spots, then 'go'", ["go", "stop"])
                for n in names:
                    log_row(prof, sess, drop_one(arm, scene, prof, sess, n))
    finally:
        scene.close()
