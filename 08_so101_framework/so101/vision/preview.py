"""`so101 detect [colors...]`: what does the camera see? (headless: prints + saves an image)"""
from __future__ import annotations

import cv2

from ..hardware.camera import Camera
from .detect import detect, draw, largest_blob


def main(a, prof, sess):
    colors = a.colors or ["orange"]
    try:
        from .mapping import PixelToBase
        mapper = PixelToBase(prof)
    except Exception as e:
        mapper = None
        sess.say(f"(no pixel->robot mapping: {e})")
    with Camera.from_profile(prof) as cam:
        f = cam.read()
    vis = f.copy()
    found = []
    for col in colors:
        dets = detect(f, prof, col, max_aspect=2.8)
        vis = draw(vis, dets)
        for d in dets:
            xy = None if mapper is None else mapper.base_xy([d.center_px])[0]
            found.append({"color": col, "px": [round(v) for v in d.center_px], "area": int(d.area),
                          "base_mm": None if xy is None else [round(v * 1000) for v in xy]})
            sess.say(f"{col:7s} px ({d.center_px[0]:.0f}, {d.center_px[1]:.0f}) area {d.area:.0f}"
                     + ("" if xy is None else f"  -> robot ({xy[0]*1000:.0f}, {xy[1]*1000:.0f}) mm"))
    c, hull = largest_blob(f, prof, prof["basket"]["color"], prof["basket"]["min_area_px"])
    if hull is not None:
        cv2.drawContours(vis, [hull], 0, (0, 200, 255), 2)
    out = prof.run_dir / "detect.jpg"
    cv2.imwrite(str(out), vis)
    sess.event("detections", items=found, image=str(out))
    sess.say(f"{len(found)} detections; annotated image: {out}")
