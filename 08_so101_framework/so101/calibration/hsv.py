"""`so101 calibrate hsv --color NAME`: measure an object's real colour and store thresholds.

Put the object ALONE in the middle of the camera view. The colour statistics of the most
colourful blob near the image centre are compared with the background, and Lab thresholds
halfway between them are written to measured.yaml (detection uses them automatically).
"""
from __future__ import annotations

import cv2
import numpy as np

from ..hardware.camera import Camera
from ..vision.detect import color_mask


def main(a, prof, sess):
    name = a.color
    sess.ask(f"put the {name} object ALONE near the middle of the camera view, then 'go'")
    with Camera.from_profile(prof) as cam:
        f = cam.read()
    lab = cv2.cvtColor(cv2.GaussianBlur(f, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(int)
    h, w = lab.shape[:2]
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    win = chroma[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    thr = np.percentile(win, 99) * 0.6
    m = (chroma > thr).astype(np.uint8) * 255
    m[: h // 4] = 0; m[3 * h // 4:] = 0; m[:, : w // 4] = 0; m[:, 3 * w // 4:] = 0
    n, labels, st, _ = cv2.connectedComponentsWithStats(m)
    if n < 2:
        raise SystemExit("no colourful object found near the image centre")
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    obj = lab[labels == k]
    bg = lab[labels == 0]
    oa, ob = np.median(obj[:, 1]), np.median(obj[:, 2])
    ba, bb = np.percentile(bg[:, 1], 99.5), np.percentile(bg[:, 2], 99.5)
    # threshold on whichever channel separates object from background best
    if oa - ba >= ob - bb:
        lo = [20, int((oa + ba) / 2), 0]; hi = [255, 255, 255]; ch = "a*"
    else:
        lo = [40, 0, int((ob + bb) / 2)]; hi = [255, 255, 255]; ch = "b*"
    rng = [{"space": "lab", "lo": lo, "hi": hi}]
    test = color_mask(f, rng)
    frac = float((test[labels == k] > 0).mean())
    cur = (prof.get("measured", {}).get("colors") or {}).get("thresholds", {})
    cur[name] = rng
    prof.save_measured("colors", {"thresholds": cur})
    cv2.imwrite(str(prof.run_dir / f"hsv_{name}.jpg"), np.hstack([f, cv2.cvtColor(test, cv2.COLOR_GRAY2BGR)]))
    sess.event("color_calibrated", color=name, channel=ch, lo=lo, object_median=[float(oa), float(ob)],
               background_p995=[float(ba), float(bb)], object_coverage=round(frac, 2))
    sess.say(f"{name}: threshold on {ch} (object median a*={oa:.0f} b*={ob:.0f}, background up to a*={ba:.0f} b*={bb:.0f}); "
             f"{frac*100:.0f}% of the object is covered. Saved to measured.yaml. Check with `so101 detect {name}`.")
