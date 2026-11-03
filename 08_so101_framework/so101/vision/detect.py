"""Coloured-object detection: colour segmentation (Lab or HSV) + contour shape tests.

Thresholds come from the profile (detection.colors), overridable by measured.yaml
(`so101 calibrate hsv`). Lessons encoded here:
- HSV failed under warm light (the wood reached orange's hue/saturation). Lab a* (red-green)
  and b* (blue-yellow) stayed separable: orange cube a* 142-155 vs wood <= 134; yellow b* ~199
  vs wood <= 160.
- Hysteresis for orange: blobs grow down to a* >= 138 but must contain a core pixel a* >= 148,
  which keeps skin (a* up to ~144) out.
- Shape: fill (area / min-rect) >= 0.70 because small cubes measure ~0.80 (pixelated edges);
  roundness (area / enclosing circle) <= 0.88 rejects discs (~1.0); aspect per object
  (cube 1.6, lego 2.8, brick 2.2 - perspective stretches rectangles).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Detection:
    color: str
    center_px: tuple
    angle_deg: float          # image-space edge angle in [0, 90)
    area: float
    box: np.ndarray           # 4x2 rotated-rect corners (px)
    score: float


def colors(prof) -> dict:
    """Profile thresholds, with measured overrides."""
    base = dict(prof["detection"]["colors"])
    base.update((prof.get("measured", {}).get("colors") or {}).get("thresholds", {}))
    return base


def color_mask(frame, ranges):
    blur = cv2.GaussianBlur(frame, (5, 5), 0)
    hsv = cv2.cvtColor(blur, cv2.COLOR_BGR2HSV)
    lab = None
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for r in ranges:
        if isinstance(r, dict):
            if lab is None:
                lab = cv2.cvtColor(blur, cv2.COLOR_BGR2LAB)
            m = cv2.inRange(lab, np.array(r["lo"]), np.array(r["hi"]))
            if "core_a" in r:
                _, labels = cv2.connectedComponents(m)
                strong = np.unique(labels[(lab[..., 1] >= r["core_a"]) & (m > 0)])
                m = np.where(np.isin(labels, strong[strong > 0]), 255, 0).astype(np.uint8)
            mask |= m
        else:
            mask |= cv2.inRange(hsv, np.array(r[:3]), np.array(r[3:]))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)


def detect(frame, prof, color, max_aspect=1.6):
    """Detections of `color`, best first, that pass the shape tests."""
    d = prof["detection"]
    mask = color_mask(frame, colors(prof)[color])
    out = []
    for cnt in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        area = cv2.contourArea(cnt)
        if not (d["min_area_px"] <= area <= d["max_area_px"]):
            continue
        (cx, cy), (w, h), ang = cv2.minAreaRect(cnt)
        if min(w, h) < 1:
            continue
        aspect, fill = max(w, h) / min(w, h), area / (w * h)
        _, rad = cv2.minEnclosingCircle(cnt)
        roundness = area / (np.pi * rad * rad)
        if aspect > max_aspect or fill < d["min_fill"] or roundness > d["max_roundness"]:
            continue
        m = cv2.moments(cnt)
        out.append(Detection(color, (m["m10"] / m["m00"], m["m01"] / m["m00"]), ang % 90.0, area,
                             cv2.boxPoints(((cx, cy), (w, h), ang)), fill / aspect))
    return sorted(out, key=lambda x: -x.score * x.area)


def largest_blob(frame, prof, color, min_area):
    """Largest blob of a colour with no shape filter (e.g. a basket). Returns (center_px, hull)."""
    m = color_mask(frame, colors(prof)[color])
    cs = [c for c in cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0] if cv2.contourArea(c) >= min_area]
    if not cs:
        return None, None
    # convex hull: an object at the rim notches the outline, and point-in-contour then wrongly
    # reported an object IN the basket as "still on the table"
    hull = cv2.convexHull(max(cs, key=cv2.contourArea))
    (cx, cy), _, _ = cv2.minAreaRect(hull)
    return (cx, cy), hull


def draw(frame, dets, hull=None):
    vis = frame.copy()
    for i, d in enumerate(dets):
        col = (0, 255, 0) if i == 0 else (0, 165, 255)
        cv2.drawContours(vis, [d.box.astype(int)], 0, col, 2)
        u, v = map(int, d.center_px)
        cv2.circle(vis, (u, v), 4, col, -1)
        cv2.putText(vis, f"{d.color} {d.angle_deg:.0f}deg", (u + 8, v - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
    if hull is not None:
        cv2.drawContours(vis, [hull], 0, (0, 200, 255), 2)
    return vis
