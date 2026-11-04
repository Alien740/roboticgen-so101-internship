"""Build the 3D-twin mesh: the URDF's STL parts, decimated and packed into one small JSON file.

    python -m so101_dashboard.mesh <folder with the SO-101 .stl files>  [--cell 0.002]

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


def build(stl_dir: Path, cell: float = 0.002, out: Path = OUT) -> dict:
    import trimesh
    from yourdfpy import URDF
    urdf = URDF.load(str(K.URDF_PATH), load_meshes=False, build_scene_graph=False)
    parts, n_in, n_out = [], 0, 0
    for link in urdf.robot.links:
        for vis in link.visuals:
            if vis.geometry is None or vis.geometry.mesh is None:
                continue
            fname = Path(vis.geometry.mesh.filename).name
            src = Path(stl_dir) / fname
            if not src.exists():
                raise FileNotFoundError(f"{src} (needed by link {link.name})")
            m = trimesh.load(src, force="mesh")
            scale = np.asarray(vis.geometry.mesh.scale if vis.geometry.mesh.scale is not None else 1.0)
            v = np.asarray(m.vertices, float) * scale
            T = vis.origin if vis.origin is not None else np.eye(4)
            v = v @ T[:3, :3].T + T[:3, 3]
            nv, nf = _cluster(v, np.asarray(m.faces), cell)
            n_in += len(m.faces); n_out += len(nf)
            parts.append({"link": link.name, "part": fname,
                          "kind": "motor" if fname.startswith(MOTOR_PARTS) else "printed",
                          "pos": _b64(nv.astype(np.float32)), "idx": _b64(nf.astype(np.uint32))})
    data = {"cell_m": cell, "triangles": n_out, "source_triangles": n_in, "parts": parts}
    out.write_text(json.dumps(data))
    return data


def load() -> dict | None:
    return json.loads(OUT.read_text()) if OUT.exists() else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stl_dir")
    ap.add_argument("--cell", type=float, default=0.002)
    a = ap.parse_args()
    d = build(Path(a.stl_dir), a.cell)
    print(f"{d['source_triangles']} -> {d['triangles']} triangles, {OUT.stat().st_size / 1e3:.0f} kB -> {OUT}")
