"""Pixel -> robot-base mapping.

Pipeline: undistort (calibrated intrinsics, or an approximate pinhole if the camera was never
calibrated) -> homography -> optional 2D rigid table->base transform.

The default calibration ("direct-auto", `so101 calibrate table`) fits the homography straight
from undistorted pixels of the cube TOP to base-frame mm, using points where the ARM itself set
the cube down - so the cube-top height and the grasp tool offset are absorbed and no
parallax term is needed. Measured accuracy on this arm: hold-one-out median ~2.8 mm.
"""
from __future__ import annotations

import json

import cv2
import numpy as np

_warned = False


def load_intrinsics(prof):
    """Calibrated K/dist, else a pinhole guess (no distortion correction)."""
    global _warned
    f = prof.path(prof["calibration"]["intrinsics_file"])
    if f.exists():
        d = np.load(f)
        return d["K"], d["dist"]
    if not _warned:
        print(f"note: no camera intrinsics ({f.name}) - using an approximate pinhole "
              f"({prof['calibration']['approx_hfov_deg']} deg HFOV, no distortion)", flush=True)
        _warned = True
    w, h = prof["camera"]["width"], prof["camera"]["height"]
    fpx = (w / 2) / np.tan(np.radians(prof["calibration"]["approx_hfov_deg"]) / 2)
    return np.array([[fpx, 0, w / 2], [0, fpx, h / 2], [0, 0, 1]], float), np.zeros(5)


def undistort_points(px, K, dist):
    pts = np.asarray(px, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.undistortPoints(pts, K, dist, P=K).reshape(-1, 2)


def fit_direct(und_px, base_m):
    """Homography undistorted px -> base mm. Returns (H, residuals_mm, hold_one_out_mm)."""
    src = np.asarray(und_px, float)
    dst = np.asarray(base_m, float) * 1000
    H, _ = cv2.findHomography(src, dst, 0)
    res = np.linalg.norm(cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2) - dst, axis=1)
    loo = []
    if len(src) >= 6:
        for i in range(len(src)):
            keep = [j for j in range(len(src)) if j != i]
            Hi, _ = cv2.findHomography(src[keep], dst[keep], 0)
            p = cv2.perspectiveTransform(src[i].reshape(1, 1, 2), Hi).reshape(2)
            loo.append(float(np.linalg.norm(p - dst[i])))
    return H, res, loo


class PixelToBase:
    def __init__(self, prof):
        f = prof.path(prof["calibration"]["table_file"])
        if not f.exists():
            raise RuntimeError("no table calibration - run `so101 calibrate table`")
        self.K, self.dist = load_intrinsics(prof)
        c = json.loads(f.read_text())
        self.H = np.array(c["H_px_to_table_mm"])
        self.TF = np.array(c.get("T_table_mm_to_base_m", [[1e-3, 0, 0], [0, 1e-3, 0]]))
        cc = c.get("camera_center_mm")
        self.cam_center = None if cc is None else np.array(cc)
        self.height_mm = 0.0

    def table_mm(self, px):
        u = undistort_points(px, self.K, self.dist).reshape(-1, 1, 2)
        p = cv2.perspectiveTransform(u, self.H).reshape(-1, 2)
        if self.cam_center is None:
            return p
        c_xy, c_h = self.cam_center[:2], abs(self.cam_center[2])
        return c_xy + (p - c_xy) * (c_h - self.height_mm) / c_h

    def base_xy(self, px):
        t = self.table_mm(px)
        return (self.TF[:, :2] @ t.T).T + self.TF[:, 2]

    def angle_to_base(self, px, angle_deg):
        a = np.radians(angle_deg)
        p0 = np.asarray(px, float)
        b = self.base_xy(np.stack([p0, p0 + 15.0 * np.array([np.cos(a), np.sin(a)])]))
        d = b[1] - b[0]
        return float(np.degrees(np.arctan2(d[1], d[0])))
