"""Build the 3D-twin mesh: the URDF's STL parts, decimated and packed into one small JSON file.

    python -m so101_dashboard.mesh <folder with the SO-101 .stl files>  [--cell 0.002]
    python -m so101_dashboard.mesh --add-missing [<folder>]   only add parts the JSON lacks

`--add-missing` keeps the committed parts and adds URDF visuals that are not in the JSON yet
(e.g. the wrist camera): from the folder when its STL is there, else as a PLACEHOLDERS box,
else skipped. Re-run it with the real STLs to replace placeholders.

The STLs (~19 MB, 380k triangles) are too heavy for a browser and are not shipped with the
framework, so the decimated result (static/twin_mesh.json) is committed instead; this script
only has to run again if the URDF or the parts change. Decimation = vertex clustering: snap
vertices to a `cell`-sized grid, merge each cell, drop collapsed triangles. Positions are
stored in the LINK frame (visual origin applied) so the browser only needs link poses.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np

from so101 import kinematics as K

OUT = Path(__file__).resolve().parent / "static" / "twin_mesh.json"
MOTOR_PARTS = ("sts3215",)            # servos are drawn dark, printed parts light
# parts whose STL is not available: drawn as a box (size m, centred on the link frame)
PLACEHOLDERS = {"wrist_camera_so101_v1.stl": (0.032, 0.032, 0.020)}   # 32x32 mm UVC module


def _cluster(v, f, cell):
    q = np.round(v / cell).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    inv = inv.ravel()
    cnt = np.bincount(inv)
    nv = np.zeros((len(cnt), 3))
    np.add.at(nv, inv, v)
    nv /= cnt[:, None]
    nf = inv[f]
    nf = nf[(nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])]
    used, remap = np.unique(nf, return_inverse=True)
    return nv[used], remap.reshape(-1, 3)


def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def _visuals():
    """(link name, STL file name, visual) for every mesh visual in the URDF."""
    from yourdfpy import URDF
    urdf = URDF.load(str(K.URDF_PATH), load_meshes=False, build_scene_graph=False)
    for link in urdf.robot.links:
        for vis in link.visuals:
            if vis.geometry is not None and vis.geometry.mesh is not None:
                yield link.name, Path(vis.geometry.mesh.filename).name, vis


def _part(link, fname, vis, src, cell):
    """One decimated part in its link frame -> (json dict, source triangles)."""
    import trimesh
    if src is None:
        m = trimesh.creation.box(extents=PLACEHOLDERS[fname])
        v, kind = np.asarray(m.vertices, float), "placeholder"
    else:
        m = trimesh.load(src, force="mesh")
        scale = np.asarray(vis.geometry.mesh.scale if vis.geometry.mesh.scale is not None else 1.0)
        T = vis.origin if vis.origin is not None else np.eye(4)
        v = (np.asarray(m.vertices, float) * scale) @ T[:3, :3].T + T[:3, 3]
        kind = "motor" if fname.startswith(MOTOR_PARTS) else "printed"
    nv, nf = _cluster(v, np.asarray(m.faces), cell)
    return ({"link": link, "part": fname, "kind": kind,
             "pos": _b64(nv.astype(np.float32)), "idx": _b64(nf.astype(np.uint32))}, len(m.faces))


def build(stl_dir: Path, cell: float = 0.002, out: Path = OUT) -> dict:
    parts, n_in, n_out = [], 0, 0
    for link, fname, vis in _visuals():
        src = Path(stl_dir) / fname
        if not src.exists():
            if fname not in PLACEHOLDERS:
                raise FileNotFoundError(f"{src} (needed by link {link})")
            src = None
        p, n = _part(link, fname, vis, src, cell)
        parts.append(p); n_in += n; n_out += len(base64.b64decode(p["idx"])) // 12
    data = {"cell_m": cell, "triangles": n_out, "source_triangles": n_in, "parts": parts}
    out.write_text(json.dumps(data))
    return data


def add_missing(stl_dir: Path | None = None, out: Path = OUT) -> tuple[list, list]:
    """Add URDF parts the committed JSON lacks (or still has as placeholders).
    Returns (added "link/part (stl|placeholder)", skipped "link/part")."""
    data = load() or {"cell_m": 0.002, "triangles": 0, "source_triangles": 0, "parts": []}
    have = {(p["link"], p["part"]) for p in data["parts"] if p["kind"] != "placeholder"}
    added, skipped = [], []
    for link, fname, vis in _visuals():
        if (link, fname) in have:
            continue
        src = Path(stl_dir) / fname if stl_dir else None
        if src is None or not src.exists():
            if fname not in PLACEHOLDERS:
                skipped.append(f"{link}/{fname}")
                continue
            src = None
        data["parts"] = [p for p in data["parts"] if (p["link"], p["part"]) != (link, fname)]
        p, n = _part(link, fname, vis, src, data["cell_m"])
        data["parts"].append(p)
        added.append(f"{link}/{fname} ({'stl' if src else 'placeholder'})")
    data["triangles"] = sum(len(base64.b64decode(p["idx"])) // 12 for p in data["parts"])
    out.write_text(json.dumps(data))
    return added, skipped


def load() -> dict | None:
    return json.loads(OUT.read_text()) if OUT.exists() else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stl_dir", nargs="?")
    ap.add_argument("--cell", type=float, default=0.002)
    ap.add_argument("--add-missing", action="store_true", help="keep existing parts, add the ones the JSON lacks")
    a = ap.parse_args()
    if a.add_missing:
        added, skipped = add_missing(Path(a.stl_dir) if a.stl_dir else None)
        print("added:", ", ".join(added) or "nothing")
        if skipped:
            print("skipped (no STL, no placeholder):", ", ".join(skipped))
        raise SystemExit(0)
    if not a.stl_dir:
        ap.error("stl_dir is required (or use --add-missing)")
    d = build(Path(a.stl_dir), a.cell)
    print(f"{d['source_triangles']} -> {d['triangles']} triangles, {OUT.stat().st_size / 1e3:.0f} kB -> {OUT}")
