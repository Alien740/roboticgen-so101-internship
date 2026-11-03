"""`so101 calibrate camera`: lens intrinsics + distortion from a printed checkerboard (headless).

No preview window is needed: hold the board in front of the camera and move/tilt it slowly.
A view is captured automatically whenever the board is found AND has moved enough since the
last capture. 15 views (tilted, near the corners, near and far) give a good calibration.

Board: count the INNER corners (a standard 10x7-square board has 9x6) and measure the square
size with a ruler. Defaults: 9x6, 25 mm (profile calibration.board).
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from ..hardware.camera import Camera


def main(a, prof, sess):
    b = prof["calibration"].get("board", {"cols": 9, "rows": 6, "square_mm": 25.0})
    pattern = (b["cols"], b["rows"])
    objp = np.zeros((pattern[0] * pattern[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:pattern[0], 0:pattern[1]].T.reshape(-1, 2) * b["square_mm"]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
    want = 15
    out = prof.run_dir / "intrinsics_views"
    out.mkdir(exist_ok=True)
    sess.ask(f"hold the {pattern[0]}x{pattern[1]}-inner-corner checkerboard ({b['square_mm']} mm squares) in front of "
             f"the camera. I capture {want} views automatically as you move and tilt it. 'go' to start")
    obj_pts, img_pts, last_c, size = [], [], None, None
    t0 = time.time()
    with Camera.from_profile(prof) as cam:
        while len(img_pts) < want and time.time() - t0 < 300:
            sess.check_stop()
            f = cam.read()
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            size = g.shape[::-1]
            ok, corners = cv2.findChessboardCorners(g, pattern, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK)
            if not ok:
                continue
            c = corners.reshape(-1, 2).mean(axis=0)
            spread = float(np.ptp(corners.reshape(-1, 2), axis=0).mean())
            if last_c is not None and np.hypot(*(c - last_c[0])) < 40 and abs(spread - last_c[1]) < 15:
                continue                                   # not moved enough: would add nothing new
            corners = cv2.cornerSubPix(g, corners, (11, 11), (-1, -1), crit)
            obj_pts.append(objp); img_pts.append(corners); last_c = (c, spread)
            cv2.imwrite(str(out / f"{len(img_pts):02d}.png"), f)
            sess.event("intrinsics_view", n=len(img_pts), of=want)
            sess.say(f"captured view {len(img_pts)}/{want} - move/tilt the board somewhere new")
    if len(img_pts) < 8:
        raise SystemExit(f"only {len(img_pts)} views captured - need >= 8. Is the corner count right ({pattern})?")
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(obj_pts, img_pts, size, None, None)
    f = prof.path(prof["calibration"]["intrinsics_file"])
    np.savez(f, K=K, dist=dist, rms=rms, size=size)
    sess.event("intrinsics_saved", rms_px=round(float(rms), 3), file=str(f))
    sess.say(f"RMS reprojection error {rms:.3f} px ({'good' if rms < 0.5 else 'ok' if rms < 1 else 'POOR - redo with more varied views'}). "
             f"Saved {f.name}. Re-run `so101 calibrate table` so the mapping uses the new lens model.")
