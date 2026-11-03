"""Shared building blocks for the manipulation tasks (pick-place, basket, multi, stack).

Lessons encoded here:
- Grasp point: the calibration was learned with the cube pressed against the FIXED jaw, so
  the fixed jaw lands touching the object. Shift toward the fixed jaw by
  (half-extent across the jaws + clearance - cube half) so it lands BESIDE the object. The
  half-extent comes from the REAL size + detected rotation (the outline includes side faces).
- Rectangular objects: turn the wrist so the jaws close across the SHORT side (pinched by
  corners, the lego slipped out). Roll stays inside the measured free range.
- Descend in a straight line; close with the object's torque; classify held/empty/jammed;
  on a miss, back off, re-detect and retry (logged as attempts).
- Release slowly and partly open, low over the target: a squeezed foam cube flicks out otherwise.
- Success is checked by the camera: the object must be gone from the table (or near the
  target, for pick-place) - "it moved" is not success.
"""
from __future__ import annotations

import csv
import time

import cv2
import numpy as np

from .. import kinematics as K
from ..hardware.camera import Camera, check_camera_still
from ..vision.detect import detect, draw, largest_blob
from ..vision.mapping import PixelToBase

CUBE_HALF_ACROSS = 0.008      # the calibration grasps had the 16 mm cube face against the fixed jaw


class Scene:
    def __init__(self, prof, sess):
        self.prof, self.sess = prof, sess
        self.cam = Camera.from_profile(prof)
        self.mapper = PixelToBase(prof)

    def close(self):
        self.cam.release()

    def check_still(self, frame):
        return check_camera_still(self.prof, frame)

    def basket(self, frame):
        b = self.prof["basket"]
        px, hull = largest_blob(frame, self.prof, b["color"], b["min_area_px"])
        return (None if px is None else self.mapper.base_xy([px])[0]), hull

    def find(self, obj_name, exclude_basket=True, frames=5, tag=None):
        """Median position over a few frames. Detections inside the basket outline are
        ignored (removes the yellow basket itself and objects already dropped).
        Returns dict(xy, box, basket, hull, frame) - xy None if not found."""
        obj = self.prof["objects"][obj_name]
        pts, boxes, baskets, frame, hull, last = [], [], [], None, None, []
        for _ in range(frames):
            frame = self.cam.read()
            bxy, hull = self.basket(frame)
            if bxy is not None:
                baskets.append(bxy)
            ds = detect(frame, self.prof, obj["color"], obj.get("max_aspect", 1.6))
            if exclude_basket and hull is not None:
                ds = [d for d in ds if cv2.pointPolygonTest(hull, d.center_px, False) < 0]
            if ds:
                pts.append(self.mapper.base_xy([ds[0].center_px])[0])
                boxes.append(self.mapper.base_xy(ds[0].box))
            last = ds
        cv2.imwrite(str(self.prof.run_dir / f"{tag or obj_name}.jpg"), draw(frame, last, hull))
        med = lambda v: np.median(v, axis=0) if len(v) >= 3 else None
        return {"xy": med(pts), "box": boxes[-1] if boxes else None, "basket": med(baskets),
                "hull": hull, "frame": frame}


# ------------------------------------------------------------------ geometry helpers
def reachable(prof, xy, heights, r_max=None):
    """None if every height is reachable top-down at xy, else the reason."""
    m = prof["motion"]
    r_max = r_max or m["reach_max"]
    r = float(np.hypot(*xy))
    if not (m["reach_min"] <= r <= r_max):
        return f"r = {r * 1000:.0f} mm (need {m['reach_min'] * 1000:.0f}-{r_max * 1000:.0f})"
    seed = {"shoulder_pan": 0, "shoulder_lift": 0, "elbow_flex": 0, "wrist_flex": 80}
    for z in heights:
        if K.ik((*xy, z), seed) is None:
            return f"no top-down solution at height {z * 1000:.0f} mm (r = {r * 1000:.0f} mm)"
    return None


def short_axis_deg(box):
    e0, e1 = box[1] - box[0], box[2] - box[1]
    short = e0 if np.linalg.norm(e0) < np.linalg.norm(e1) else e1
    return float(np.degrees(np.arctan2(short[1], short[0])) % 180)


def half_across(box, size, jdir):
    """Half the object's real extent along the jaw axis, given its detected rotation."""
    e0, e1 = box[1] - box[0], box[2] - box[1]
    long_dir = e0 if np.linalg.norm(e0) >= np.linalg.norm(e1) else e1
    long_dir = long_dir / np.linalg.norm(long_dir)
    c = abs(float(long_dir @ jdir))
    return 0.5 * (size[0] * c + size[1] * np.sqrt(max(0.0, 1 - c * c)))


def travel_z(prof):
    b = prof["basket"]
    return max(prof["motion"]["travel_z"], b["height"] + b["rim_clear"] + 0.03)


# ------------------------------------------------------------------ grasp / release
def grasp(arm, scene, obj_name, found, row):
    """Pick the object at found['xy']. Fills row(attempts, grip_pos, roll_deg, half_across_mm).
    Returns (ok, tool_xy_used, half_across_m)."""
    prof, sess = arm.prof, scene.sess
    obj, g, w = prof["objects"][obj_name], prof["gripper"], prof["wrist"]
    tz = travel_z(prof)
    xy, box = found["xy"], found["box"]
    pos, x, y, half = 0.0, xy[0], xy[1], obj["size"][1] / 2
    for attempt in range(1, prof["tasks"]["max_attempts"] + 1):
        row["attempts"] = attempt
        seed = {"shoulder_pan": 0, "shoulder_lift": 0, "elbow_flex": 0, "wrist_flex": 80}
        a0 = K.ik((*xy, obj["grasp_z"]), seed)[0]
        roll = arm.angles()["wrist_roll"]
        if obj.get("align") and box is not None:
            roll = K.roll_for_axis(a0, short_axis_deg(box), 180.0, (w["roll_min_ok"], w["roll_max_ok"]))
            row["roll_deg"] = round(roll, 1)
        jdir = K.jaw_dir(K.ik((*xy, obj["grasp_z"]), a0, roll=roll)[0])
        half = half_across(box, obj["size"], jdir) if box is not None else obj["size"][1] / 2
        x, y = xy + jdir * (half + obj.get("clearance", g["cube_clearance"]) - CUBE_HALF_ACROSS)
        row["half_across_mm"] = round(half * 1000, 1)
        sess.event("grasp_attempt", object=obj_name, attempt=attempt, target_mm=[round(x * 1000), round(y * 1000)],
                   roll=round(roll, 1), half_across_mm=row["half_across_mm"])
        arm.open()
        cur = arm.angles()
        sol = K.ik((x, y, tz), cur, roll=roll, gripper=cur["gripper"])
        if sol is None:
            row["failure"] = "IK failed for the approach"; return False, (x, y), half
        arm.move_joints({f"{k}.pos": v for k, v in sol[0].items() if k != "gripper"})
        arm.move_linear((x, y, obj["grasp_z"]), duration=1.8)
        pos = arm.close(torque=obj.get("torque", g["torque"]))
        row["grip_pos"] = round(pos, 1)
        state = arm.holding(pos)
        sess.event("grasp_result", attempt=attempt, gripper=round(pos, 1), state=state)
        if state == "held":
            return True, (x, y), half
        arm.open()
        arm.move_linear((x, y, tz), duration=1.5)
        if attempt == prof["tasks"]["max_attempts"]:
            break
        arm.park()
        f = scene.find(obj_name)
        if f["xy"] is None or reachable(prof, f["xy"], (tz, obj["grasp_z"])):
            break
        xy, box = f["xy"], f["box"]
    kind = "jaw jammed on object" if pos > g["jam_threshold"] else "grasp missed"
    row["failure"] = f"{kind} x{row['attempts']} (gripper {pos:.1f})"
    return False, (x, y), half


def carry_and_release(arm, xy_tool, z_release, slow=True):
    """Lift, travel, lower in a straight line, release. Returns False if dropped en route."""
    prof = arm.prof
    tz, b, g = travel_z(prof), prof["basket"], prof["gripper"]
    pos = arm.tool_pose()[0]
    arm.move_linear((pos[0], pos[1], tz), duration=1.5)
    arm.move_xyz((*xy_tool, tz))
    held = arm.holding() == "held"
    arm.move_linear((*xy_tool, z_release), duration=1.2)
    if slow:
        arm.gripper(b["release_open"], duration=b["release_s"]); time.sleep(0.5)
    else:
        arm.open()
    arm.set_grip_torque(g["torque"])
    arm.move_linear((*xy_tool, tz), duration=1.0)
    return held


# ------------------------------------------------------------------ logging
FIELDS = ["trial", "timestamp", "task", "object", "detected", "x_mm", "y_mm", "attempts", "grasped",
          "grip_pos", "roll_deg", "half_across_mm", "success", "error_mm", "cycle_time_s", "failure", "notes"]


def new_row(task, obj):
    r = {k: "" for k in FIELDS}
    r.update(timestamp=time.strftime("%Y-%m-%d %H:%M:%S"), task=task, object=obj, detected=0, grasped=0, success=0)
    return r


def log_row(prof, sess, row):
    f = prof.run_dir / f"{row['task']}_trials.csv"
    new = not f.exists()
    row["trial"] = 1 if new else sum(1 for _ in open(f)) - 1 + 1
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    sess.event("trial", **row)
    sess.say(f"RESULT {row['task']} #{row['trial']} {row['object']}: success={row['success']} "
             f"attempts={row['attempts']} err={row['error_mm']} mm cycle={row['cycle_time_s']} s {row['failure']}")
