"""Picture -> pen strokes, for the drawing task.

Three modes:
- "lines": line art, sketches, logos, stippled prints. Dark ink is thresholded (adaptive, so uneven
  lighting in a phone photo of a drawing is fine), specks such as stipple dots are removed, and the
  ink is thinned to one-pixel centre lines, so a thick line becomes ONE pen stroke, not its outline.
- "photo": photos. Edges (Canny on a smoothed image), then thinned and traced the same way.
- "shade": stippled / shaded pictures (dots, tones): the lines as in "lines", plus hatching (parallel
  lines, crossed where darkest) where the dots are dense - the way pen plotters render tone.

The traced polylines go through the plotter clean-up chain (simplify, drop specks, join touching
ends, nearest-neighbour order) from so101.tasks.pathtools.

Output strokes are in image pixels (x right, y down) of the resized working image.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from ..tasks import pathtools as P

WORK_PX = 400            # the longer side of the working image; finer than a pen line at 120 mm
NB = [(-1, 0), (0, 1), (1, 0), (0, -1), (-1, 1), (1, 1), (1, -1), (-1, -1)]   # 4-neighbours first


def decode(data: bytes):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("not an image (use PNG or JPEG)")
    if img.ndim == 3 and img.shape[2] == 4:                       # transparent PNG: paint on white
        a = img[:, :, 3:4].astype(np.float32) / 255
        img = (img[:, :, :3] * a + 255 * (1 - a)).astype(np.uint8)
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _resize(gray, px):
    h, w = gray.shape
    k = px / max(h, w)
    return cv2.resize(gray, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)


def ink_mask(gray, detail=0.5):
    """Line art -> boolean ink mask without specks."""
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    k = max(15, (max(g.shape) // 12) | 1)                         # wider than any pen line in the picture
    bg = cv2.morphologyEx(g, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    norm = cv2.divide(g, bg, scale=255)                           # flatten uneven light (phone photos)
    _, m = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    m = cv2.bitwise_and(m, (norm < 225).astype(np.uint8) * 255)    # a blank page has no ink
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    min_area = int(round(60 * (1.0 - detail) + 6))                # stipple dots / dust below this go
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return keep[lab]


def edge_mask(gray, detail=0.5):
    """Photo -> boolean edge mask (outlines and strong features)."""
    g = cv2.bilateralFilter(gray, 7, 50, 50)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    med = float(np.median(g))
    k = 1.25 - 0.9 * detail                                      # more detail -> lower thresholds
    e = cv2.Canny(g, int(max(10, 0.66 * med * k)), int(min(255, 1.33 * med * k + 20)))
    e = cv2.dilate(e, np.ones((2, 2), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(e, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= int(40 * (1.0 - detail) + 10)
    return keep[lab]


def thin(mask):
    m = mask.astype(np.uint8) * 255
    if hasattr(cv2, "ximgproc"):
        return cv2.ximgproc.thinning(m, thinningType=cv2.ximgproc.THINNING_ZHANGSUEN) > 0
    return _zhang_suen(mask)


def _zhang_suen(mask):
    """Zhang-Suen thinning in numpy (used when OpenCV has no contrib module)."""
    im = np.pad(mask.astype(np.uint8), 1)
    while True:
        changed = False
        for step in (0, 1):
            p = [np.roll(np.roll(im, -dy, 0), -dx, 1) for dy, dx in
                 [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]]
            b = sum(p)
            seq = p + [p[0]]
            a = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
            if step == 0:
                c = (p[0] * p[2] * p[4] == 0) & (p[2] * p[4] * p[6] == 0)
            else:
                c = (p[0] * p[2] * p[6] == 0) & (p[0] * p[4] * p[6] == 0)
            rm = (im == 1) & (b >= 2) & (b <= 6) & (a == 1) & c
            if rm.any():
                im[rm] = 0
                changed = True
        if not changed:
            return im[1:-1, 1:-1].astype(bool)


def trace(skel):
    """One-pixel skeleton -> polylines: walk from the line ends first, then the closed loops."""
    on = skel.copy()
    h, w = on.shape
    pad = np.pad(on.astype(np.uint8), 1)
    deg = sum(np.roll(np.roll(pad, dy, 0), dx, 1) for dy, dx in NB)[1:-1, 1:-1] * on
    ys, xs = np.nonzero(on)
    ends = [(y, x) for y, x in zip(ys, xs) if deg[y, x] == 1]
    rest = list(zip(ys, xs))
    seen = np.zeros_like(on)
    lines = []

    def walk(y, x):
        pts = [(x, y)]
        seen[y, x] = True
        while True:
            for dy, dx in NB:
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and on[ny, nx] and not seen[ny, nx]:
                    y, x = ny, nx
                    seen[y, x] = True
                    pts.append((x, y))
                    break
            else:
                return pts

    for y, x in ends + rest:
        if not seen[y, x]:
            pts = walk(y, x)
            if len(pts) >= 2:
                lines.append([(float(a), float(b)) for a, b in pts])
    return lines


def _hatch(mask, spacing, angle_deg, min_run=3):
    """Parallel lines at angle_deg, `spacing` px apart, clipped to mask; alternate lines reversed."""
    h, w = mask.shape
    a = math.radians(angle_deg)
    d, n = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    R = math.hypot(w, h) / 2
    cx, cy = w / 2, h / 2
    t = np.arange(-R, R, 1.0)
    out = []
    for i, off in enumerate(np.arange(-R, R, spacing)):
        xs = cx + n[0] * off + d[0] * t
        ys = cy + n[1] * off + d[1] * t
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        inside = np.zeros_like(ok)
        inside[ok] = mask[ys[ok].astype(int), xs[ok].astype(int)]
        edges = np.flatnonzero(np.diff(np.r_[0, inside.astype(np.int8), 0]))
        for s_, e_ in zip(edges[::2], edges[1::2]):
            if e_ - s_ >= min_run:
                seg = [(float(xs[s_]), float(ys[s_])), (float(xs[e_ - 1]), float(ys[e_ - 1]))]
                out.append(seg[::-1] if i % 2 else seg)
    return out


def shade_strokes(gray, detail=0.5):
    """Centre lines of the drawn lines + hatching where the dots are dense (darker = cross-hatched)."""
    lines = trace(thin(ink_mask(gray, detail)))
    dens = cv2.GaussianBlur(ink_mask(gray, 1.0).astype(np.float32), (0, 0), 3.0)   # every dot counts
    spacing = 7.0 - 3.0 * detail                                  # px between hatch lines
    lines += _hatch(dens > 0.18, spacing, 50)
    lines += _hatch(dens > 0.45, spacing, -40)
    return lines, dens > 0.18


def vectorize(gray, mode="lines", detail=0.5, work_px=WORK_PX):
    """Grayscale image -> {"strokes", "width", "height", "mask"}; strokes in working-image px."""
    detail = float(min(1.0, max(0.0, detail)))
    g = _resize(gray, work_px)
    if mode == "shade":
        lines, mask = shade_strokes(g, detail)
    else:
        mask = edge_mask(g, detail) if mode == "photo" else ink_mask(g, detail)
        lines = trace(thin(mask))
    eps = 1.4 - 0.9 * detail                                      # px; RDP tolerance
    min_len = 10 * (1.0 - detail) + 3                             # px; shorter lines are noise
    strokes = P.clean(lines, eps=eps, min_len=min_len, join_tol=2.5)
    return {"strokes": [[[round(x, 1), round(y, 1)] for x, y in st] for st in strokes],
            "width": g.shape[1], "height": g.shape[0], "mask": mask}


def stats(strokes, size_mm, width, height, speed_mm_s=10.0, travel_mm_s=30.0, lift_s=3.2):
    """Ink and pen-up lengths (mm at the drawn size) and a time estimate for the robot."""
    k = size_mm / max(width, height)
    ink = sum(P.length(st) for st in strokes) * k
    trav = P.travel(strokes) * k
    secs = ink / speed_mm_s + trav / travel_mm_s + lift_s * len(strokes)
    return {"strokes": len(strokes), "points": sum(map(len, strokes)), "ink_mm": round(ink),
            "travel_mm": round(trav), "est_s": round(secs)}
