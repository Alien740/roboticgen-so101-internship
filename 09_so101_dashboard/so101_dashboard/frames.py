"""Pose of every URDF link (for the 3D twin), built on the framework's kinematics.

The framework's fk() only follows the chain to the tool point; the twin also needs the base
parts and the moving jaw, so this walks the whole joint tree.
"""
from __future__ import annotations

import math

import numpy as np

from so101 import kinematics as K

_TREE = None


def _tree():
    """Every URDF joint (incl. the moving jaw and fixed joints), parents before children."""
    global _TREE
    if _TREE is None:
        from yourdfpy import URDF
        urdf = URDF.load(str(K.URDF_PATH), load_meshes=False, build_scene_graph=False)
        children = {j.child for j in urdf.robot.joints}
        done = {j.parent for j in urdf.robot.joints if j.parent not in children}
        root = next(iter(done))
        todo, out = list(urdf.robot.joints), []
        while todo:
            ready = [j for j in todo if j.parent in done]
            if not ready:
                break
            for j in ready:
                out.append(j); done.add(j.child); todo.remove(j)
        _TREE = (out, root)
    return _TREE


def link_frames(angles: dict) -> dict:
    """4x4 base-frame pose of every link. The gripper (lerobot 0-100, 0 = closed) is mapped
    linearly onto the URDF jaw limits."""
    joints, root = _tree()
    frames = {root: np.eye(4)}
    for j in joints:
        T = frames[j.parent] @ j.origin
        if j.type in K.MOVABLE:
            if j.name == "gripper" and j.limit is not None:
                g = min(max(angles.get("gripper", 0.0), 0.0), 100.0) / 100.0
                theta = j.limit.lower + (j.limit.upper - j.limit.lower) * g
            else:
                theta = math.radians(angles.get(j.name, 0.0))
            T = T @ K._rot_axis(j.axis, theta)
        frames[j.child] = T
    return frames
