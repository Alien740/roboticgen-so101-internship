"""The AI tab's eyes: the overhead camera's latest frame (the page sends it) -> objects on the table in
robot coordinates, with the framework's own detector and table calibration (the homography the arm
measured by setting the cube down at 21 spots: `so101 calibrate table`)."""
from __future__ import annotations

import math
import time

import cv2
import numpy as np

from so101 import profile
from so101.vision.detect import detect
from so101.vision.mapping import PixelToBase

# what the user calls it -> detector colour (the kit's cube is detected as "orange": Lab a* rejects skin and wood)
COLORS = {"red": "orange", "orange": "orange", "yellow": "yellow"}
NAMES = {"orange": "red cube", "yellow": "yellow block"}
FRESH_S = 3.0


def frame(get):
    t, data = get()
    if data is None or time.time() - t > FRESH_S:
        raise RuntimeError("no picture from the overhead camera - the dashboard page must be open with Camera 1 on")
    f = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if f is None:
        raise RuntimeError("bad camera frame")
    return f


def find(get, prof_name, colors=("orange", "yellow")):
    """Objects seen now, biggest first per colour: [{what, color, x, y, reach_cm, base_deg, edge_deg}]."""
    prof = profile.load(prof_name)
    try:
        m = PixelToBase(prof)
    except RuntimeError:
        raise RuntimeError("the overhead camera is not calibrated - run Calibrate > Table calibration "
                           "(the arm carries the cube to 21 spots, about 12 minutes)") from None
    f = frame(get)
    out = []
    for color in colors:
        obj = next((o for o in prof["objects"].values() if o["color"] == color), {})
        for d in detect(f, prof, color, obj.get("max_aspect", 1.6))[:3]:
            x, y = m.base_xy(np.array([d.center_px], float))[0]
            out.append({"what": NAMES.get(color, color), "color": color, "x": float(x), "y": float(y),
                        "reach_cm": round(math.hypot(x, y) * 100, 1), "base_deg": round(math.degrees(math.atan2(y, x)), 1),
                        "edge_deg": round(m.angle_to_base(d.center_px, d.angle_deg), 1), "px": [round(v) for v in d.center_px]})
    return out
