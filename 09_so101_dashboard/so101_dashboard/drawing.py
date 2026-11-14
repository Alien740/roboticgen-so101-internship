"""Draw tab, "From a picture": picture -> pen strokes -> a dry run of the arm -> the real drawing.

- vectorize(): the uploaded picture becomes strokes (so101.vision.vectorize: line art, photo edges
  or shading), returned in picture pixels for the preview and as u/v (u = left, v = away) for the
  robot, the same frame the sketch pad uses.
- plan(): the framework's own planner (so101.tasks.draw.simulate) runs the drawing without the arm,
  at the spot of the last hand placement. The result is a timeline for the 3D twin: link frames,
  pen up/down and the tip, so the page can play the whole drawing before the pen goes down.
"""
from __future__ import annotations

import base64

from so101 import profile
from so101.tasks import draw as D
from so101.vision import vectorize as V

from .frames import link_frames

MAX_IMAGE_BYTES = 15_000_000
MAX_KEYFRAMES = 600
PEN_GRIPPER = 8.0                         # gripper % while holding the pen, for the twin


def vectorize(body):
    data = body["image"]
    if "," in data[:100]:
        data = data.split(",", 1)[1]                              # data URL -> base64
    raw = base64.b64decode(data)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("picture too large (max 15 MB)")
    mode = body.get("mode", "lines")
    if mode not in ("lines", "photo", "shade"):
        raise ValueError("mode must be lines | photo | shade")
    r = V.vectorize(V.decode(raw), mode, float(body.get("detail", 0.5)))
    size = float(body.get("size_mm", 100))
    st = r["strokes"]
    if not st:
        raise ValueError("no lines found in this picture - try 'photo' mode or more detail")
    return {"strokes": st, "width": r["width"], "height": r["height"],
            "uv": [[[-x, -y] for x, y in s] for s in st],         # picture -> u (left), v (away)
            "stats": V.stats(st, size, r["width"], r["height"])}


def _frames(angles):
    F = link_frames({**angles, "gripper": PEN_GRIPPER})
    return {n: [round(float(x), 4) for x in T.T.ravel()] for n, T in F.items()}


def plan(prof_name, body):
    prof = profile.load(prof_name)                                # fresh: picks up the last hand placement
    size = min(float(body.get("size_mm", 100)), prof["drawing"]["max_size_mm"])
    strokes = [st for st in body["strokes"] if len(st) >= 2]
    if not strokes or sum(map(len, strokes)) > 20000:
        raise ValueError("nothing to plan (or over 20000 points)")
    sim = D.simulate(prof, D.fit_strokes(strokes, size))
    fr = sim["frames"]
    keep = range(len(fr)) if len(fr) <= MAX_KEYFRAMES else \
        sorted({round(i * (len(fr) - 1) / (MAX_KEYFRAMES - 1)) for i in range(MAX_KEYFRAMES)})
    ps = D.pen_setup(prof)
    return {
        "ok": sim["ok"], "error": sim["error"], "size_mm": size,
        "placed": ps["placed"], "center": [round(float(v), 4) for v in ps["center"]],
        "paper_z": ps["paper_z"], "seconds": round(fr[-1][0]) if fr else 0,
        # every planned tip point (for the ink lines) and a decimated keyframe track (for the twin)
        "tips": [[round(t, 2), int(down), *[round(v, 4) for v in p], sim["stroke_of_frame"][i]]
                 for i, (t, _, down, p) in enumerate(fr)],
        "keys": [{"t": round(fr[i][0], 2), "f": _frames(fr[i][1])} for i in keep],
    }
