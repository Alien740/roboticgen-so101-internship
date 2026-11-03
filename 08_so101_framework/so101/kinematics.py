"""SO-101 kinematics: URDF forward kinematics + the IK solvers proven on the real arm.

Joint angles are lerobot degrees (use_degrees=True). Positions are metres in the robot
base frame (x forward, y left, z up). The tool point is the URDF gripper_frame_link
(close to the FIXED jaw tip; tool +x points toward the fixed jaw).

Lessons encoded here (see docs/TROUBLESHOOTING.md):
- Solve with costs in mm^2: in m^2 a 1 mm error is 1e-6, below L-BFGS's gradient
  tolerance, so it stopped early (1-3 mm residuals, false "unreachable" points).
- Score candidates on position AND pitch together; picking only on pitch returned
  3 mm-off poses and bent straight-line moves.
- For paths, chain single-seed solves (stay on one IK branch); re-running the
  multi-seed search per waypoint made the jaws visibly shake.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize

from .profile import FRAMEWORK_ROOT

URDF_PATH = FRAMEWORK_ROOT / "so101" / "assets" / "so101_new_calib.urdf"
ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex"]
J5 = ARM + ["wrist_roll"]
JOINT_LIMITS = {
    "shoulder_pan": (-100.0, 100.0), "shoulder_lift": (-100.0, 100.0),
    "elbow_flex": (-100.0, 100.0), "wrist_flex": (-90.0, 90.0),
    "wrist_roll": (-180.0, 180.0), "gripper": (0.0, 100.0),
}
MOVABLE = ("revolute", "continuous")
_CHAIN = None


# ------------------------------------------------------------------ forward kinematics
def _rot_axis(axis, theta):
    axis = np.asarray(axis, float)
    n = np.linalg.norm(axis)
    if n < 1e-9:
        return np.eye(4)
    x, y, z = axis / n
    c, s = math.cos(theta), math.sin(theta)
    T = np.eye(4)
    T[:3, :3] = [[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                 [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                 [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]]
    return T


def chain():
    """Serial joint chain base -> gripper_frame_link (meshes are not needed)."""
    global _CHAIN
    if _CHAIN is None:
        from yourdfpy import URDF
        urdf = URDF.load(str(URDF_PATH), load_meshes=False, build_scene_graph=False)
        by_parent = {}
        for j in urdf.robot.joints:
            by_parent.setdefault(j.parent, []).append(j)
        children = {j.child for j in urdf.robot.joints}
        cur = next(j.parent for j in urdf.robot.joints if j.parent not in children)
        out, seen = [], set()
        while by_parent.get(cur):
            j = next((c for c in by_parent[cur] if c.child not in seen), None)
            if j is None:
                break
            out.append(j); seen.add(j.child); cur = j.child
        _CHAIN = out
    return _CHAIN


def fk(angles: dict) -> np.ndarray:
    """4x4 tool pose for joint angles in degrees."""
    T = np.eye(4)
    for j in chain():
        T = T @ j.origin
        if j.type in MOVABLE:
            T = T @ _rot_axis(j.axis, math.radians(angles.get(j.name, 0.0)))
    return T


def pitch_deg(T) -> float:
    f = T[:3, 2]
    return math.degrees(math.atan2(f[2], math.hypot(f[0], f[1])))


# ------------------------------------------------------------------ top-down IK (4 joints)
def ik(xyz, seed, pitch=-90.0, roll=0.0, gripper=50.0):
    """Tool tip at xyz with the given pitch (relaxed toward -60 if unreachable).
    Returns (angles, pos_err_m, pitch_deg) or None."""
    target = np.asarray(xyz, float)
    pan = math.degrees(math.atan2(target[1], target[0]))
    seeds = [[seed.get(n, 0.0) for n in ARM]] + [
        [pan, l, e, 85.0] for l, e in [(10, -10), (30, -30), (-10, 20), (0, 0), (40, -60), (-30, 50)]]
    for want in (pitch, pitch + 10, pitch + 20, pitch + 30):
        best = None
        for q0 in seeds:
            def unpack(q):
                return {**dict(zip(ARM, q)), "wrist_roll": roll, "gripper": gripper}

            def cost(q):
                T = fk(unpack(q))
                e = np.linalg.norm(T[:3, 3] - target)
                return e * e + 2e-8 * (pitch_deg(T) - want) ** 2

            r = minimize(cost, q0, bounds=[JOINT_LIMITS[n] for n in ARM], method="L-BFGS-B",
                         options={"maxiter": 400, "ftol": 1e-12})
            T = fk(unpack(r.x))
            err, p = float(np.linalg.norm(T[:3, 3] - target)), pitch_deg(T)
            score = err * 1000 + 0.3 * abs(p - want)
            if err < 0.004 and (best is None or score < best[3]):
                best = (unpack(r.x), err, p, score)
            if q0 is seeds[0] and best is not None and best[1] < 0.001 and abs(best[2] - want) < 5:
                return best[:3]
        if best is not None and abs(best[2] - want) < 8:
            return best[:3]
    return None


def ik_local(xyz, seed, pitch_target, w_pitch=10.0):
    """Single-seed IK holding a pitch (mm^2 cost). Returns (angles, pos_err_m)."""
    target = np.asarray(xyz, float)
    fixed = {k: seed[k] for k in ("wrist_roll", "gripper")}

    def unpack(q):
        return {**dict(zip(ARM, q)), **fixed}

    def cost(q):
        T = fk(unpack(q))
        return (np.linalg.norm(T[:3, 3] - target) * 1000) ** 2 + w_pitch * (pitch_deg(T) - pitch_target) ** 2

    r = minimize(cost, [seed[n] for n in ARM], bounds=[JOINT_LIMITS[n] for n in ARM],
                 method="L-BFGS-B", options={"maxiter": 200, "ftol": 1e-12})
    a = unpack(r.x)
    return a, float(np.linalg.norm(fk(a)[:3, 3] - target))


def jaw_dir(angles) -> np.ndarray:
    """Unit base-frame xy direction of tool +x (toward the FIXED jaw)."""
    v = fk(angles)[:2, 0]
    return v / np.linalg.norm(v)


def roll_for_axis(angles, axis_deg, period=180.0, roll_limits=(-180.0, 180.0)):
    """Wrist roll that makes the jaws close along axis_deg (period 90 for a cube, 180 for a
    rectangle). Respects the measured roll limits (this arm jams near +80 deg)."""
    a0, a1 = dict(angles, wrist_roll=0.0), dict(angles, wrist_roll=10.0)
    ang = lambda a: math.degrees(math.atan2(*fk(a)[[1, 0], 0]))
    base = ang(a0)
    sign = 1.0 if ((ang(a1) - base + 180) % 360 - 180) > 0 else -1.0
    delta = (axis_deg - base) % period
    if delta > period / 2:
        delta -= period
    cands = [sign * (delta + k * period) for k in (-2, -1, 0, 1, 2)]
    lo, hi = roll_limits
    ok = [c for c in cands if lo <= c <= hi]
    if ok:
        return min(ok, key=abs)
    # free range narrower than the period (this arm: -100..75 for period 180): clamp the
    # nearest candidate to the limit - jaws end up a few degrees off instead of jamming
    best = min(cands, key=lambda c: max(lo - c, c - hi))
    return min(max(best, lo), hi)


# ------------------------------------------------------------------ pen IK (5 joints)
def pen_down_z(T, pen_dir) -> float:
    """z of the pen axis in the base frame (-1 = pointing straight down)."""
    return float((T[:3, :3] @ np.asarray(pen_dir, float))[2])


def pen_tip(T, pen_dir, length):
    """Pen TIP position: tool point + length along the pen axis.

    The pen cannot be held fully vertical everywhere (wrist_flex hits its -90 deg stop ->
    10-15 deg lean over a 60 mm circle at 29 cm), and with an 8 cm pen a 12 deg lean moves
    the tip ~17 mm. Solving for the TIP (length from the touch-off) keeps shapes true."""
    return T[:3, 3] + length * (T[:3, :3] @ np.asarray(pen_dir, float))


def pen_ik_local(xyz, seed, pen_dir, w_vert=400.0, length=0.0):
    """Single-seed 5-joint solve placing the pen TIP (tool point if length=0) at xyz,
    preferring a vertical pen. Returns (angles, tip_err_m, verticality)."""
    t = np.asarray(xyz, float)

    def cost(q):
        T = fk({**seed, **dict(zip(J5, q))})
        return np.sum(((pen_tip(T, pen_dir, length) - t) * 1000) ** 2) + w_vert * (pen_down_z(T, pen_dir) + 1) ** 2

    r = minimize(cost, [seed[n] for n in J5], bounds=[JOINT_LIMITS[n] for n in J5],
                 method="L-BFGS-B", options={"maxiter": 300})
    A = {**seed, **dict(zip(J5, r.x))}
    T = fk(A)
    return A, float(np.linalg.norm(pen_tip(T, pen_dir, length) - t)), float(-pen_down_z(T, pen_dir))


def pen_ik(xyz, seed, pen_dir, min_vert=0.96, roll_limits=(-180.0, 180.0), length=0.0):
    """Multi-seed pen-down IK keeping the current roll family. Raises if not reachable."""
    pan = math.degrees(math.atan2(xyz[1], xyz[0]))
    roll = seed["wrist_roll"]
    seeds = [seed] + [{**seed, "shoulder_pan": pan, "shoulder_lift": l, "elbow_flex": e,
                       "wrist_flex": w, "wrist_roll": roll}
                      for l, e, w in [(-20, 20, 0), (0, -10, 0), (-40, 50, 0), (10, -30, 10), (-30, 30, -10)]]
    best = None
    for sd in seeds:
        A, err, vert = pen_ik_local(xyz, sd, pen_dir, length=length)
        if not (roll_limits[0] <= A["wrist_roll"] <= roll_limits[1]):
            continue
        score = err * 1000 + 50 * (1 - vert) + 0.02 * abs(A["wrist_flex"])
        if best is None or score < best[3]:
            best = (A, err, vert, score)
    if best is None or best[1] > 0.002 or best[2] < min_vert:
        e = "no solution" if best is None else f"err {best[1]*1000:.1f} mm, lean {math.degrees(math.acos(min(1, best[2]))):.0f} deg"
        raise RuntimeError(f"pen cannot point straight down at {np.round(xyz, 3)} ({e})")
    return best[0]


def pen_path(seed, pts, pen_dir, min_vert=0.96, max_jump=12.0, length=0.0):
    """Chain single-seed pen solves along dense TIP points; refuse leaning poses / branch jumps."""
    out = []
    for p in pts:
        A, err, vert = pen_ik_local(p, seed, pen_dir, length=length)
        if err > 0.002 or vert < min_vert:
            raise RuntimeError(f"drawing point {np.round(p, 3)} unreachable with a vertical pen")
        out.append(A); seed = A
    jumps = [max(abs(out[i + 1][j] - out[i][j]) for j in J5) for i in range(len(out) - 1)]
    if jumps and max(jumps) > max_jump:
        raise RuntimeError(f"path has a {max(jumps):.0f} deg joint jump")
    return out


def dense(points, step=0.002):
    """Resample a polyline so consecutive points are <= step apart."""
    out = [np.asarray(points[0], float)]
    for p in points[1:]:
        p = np.asarray(p, float)
        n = max(1, int(math.ceil(np.linalg.norm(p - out[-1]) / step)))
        a = out[-1]
        out += [a + (p - a) * k / n for k in range(1, n + 1)]
    return out
