"""`so101 draw`: draw shapes with a pen held ACROSS the jaws (like a cigarette).

Session (answer each prompt; 'stop' aborts motion at any time in agent mode, Ctrl+C in a terminal):
  1. load   - gripper opens unrotated; put the pen across the jaws, tip out 5-7 cm   ('roll <deg>' first if needed)
  2. place  - (default) the arm goes limp while the gripper keeps the pen: move it by hand so the pen
             points straight down with its tip ON the paper where the drawing should go, then 'go'.
             That one pose gives the pen direction, the drawing spot and the pen length.
             (the older steps below stay available: 'tilt' + 'probe' at the drawing menu)
  2. tilt   - wrist tilts up so the pen points down; confirm by EYE  ('roll <deg>', 'flip', 'nudge <away> <left>')
             -> the confirmed pen direction is saved to measured.yaml for next time
  3. height - say how high the tip is (mm), then jog: 'down', 'down 3', 'up', ... 'touch'
  4. draw   - 'level 60' (touch 3 corners: follows the paper's slope), 'draw circle 60', 'press 3.5',
             'speed 10', 'move <away_mm> <left_mm>', 'drawfile <name>' (strokes from
             runs/<profile>/drawings/<name>.json, e.g. a sketch from the dashboard), 'place', 'quit'

Lessons encoded here (docs/TROUBLESHOOTING.md has the full story):
- The URDF wrist-roll zero is ~90 deg off this arm: "pen down" = gripper UNROTATED + wrist
  tilted up. The operator confirms by eye and the pen axis is learned from that pose.
- Roll jams near +80 deg: roll stays in the measured free range.
- Contact detection from motor load FAILED in this pose (push goes into the wrist/grip):
  height is found by a manual jog/touch, never by pressing until something gives.
- The pen leans up to ~15 deg (wrist_flex at its stop), so the TIP is aimed (pen length from the
  touch-off), not the tool point.
- Paper height varied 5.5 mm over 6 cm: 'level' touches 3 corners and the pen follows the plane.
- Smoothness: 30 Hz streaming of a ~0.4 mm path, planned BEFORE the pen goes down.
- Gripper: a long full-force clamp trips the servo's overload protection -> hold() backs off.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np

from .. import kinematics as K
from . import pathtools as P

DOWN = np.array((0.0, 0.0, -1.0))
PEN_NOMINAL = 0.03     # m; hand placement: pen length past the tool point (any value works with a vertical pen)


# ------------------------------------------------------------------ shapes (mm, centred on 0,0)
def shape(name, size):
    r = size / 2
    circ = lambda rad, n=90, a0=0.0, a1=2 * math.pi: [(rad * math.cos(t), rad * math.sin(t)) for t in np.linspace(a0, a1, n)]
    if name == "square":
        return [[(-r, -r), (r, -r), (r, r), (-r, r), (-r, -r)]]
    if name == "circle":
        return [circ(r)]
    if name == "star":
        return [[(r * math.cos(math.pi / 2 + k * 4 * math.pi / 5), r * math.sin(math.pi / 2 + k * 4 * math.pi / 5)) for k in range(6)]]
    if name == "spiral":
        t = np.linspace(0, 6 * math.pi, 300)
        return [[(r * tt / t[-1] * math.cos(tt), r * tt / t[-1] * math.sin(tt)) for tt in t]]
    if name == "smiley":
        return [circ(r), [(x - 0.35 * r, y + 0.3 * r) for x, y in circ(0.08 * size, 24)],
                [(x + 0.35 * r, y + 0.3 * r) for x, y in circ(0.08 * size, 24)],
                [(0.55 * r * math.cos(t), 0.55 * r * math.sin(t) - 0.05 * r) for t in np.linspace(math.pi * 1.15, math.pi * 1.85, 40)]]
    if name == "house":
        return [[(-r, -r), (r, -r), (r, 0.2 * r), (-r, 0.2 * r), (-r, -r)], [(-r, 0.2 * r), (0, r), (r, 0.2 * r)],
                [(-0.25 * r, -r), (-0.25 * r, -0.35 * r), (0.15 * r, -0.35 * r), (0.15 * r, -r)]]
    raise ValueError(f"unknown shape '{name}' (square circle star spiral smiley house)")


SHAPES = ("square", "circle", "star", "spiral", "smiley", "house")


def to_base(strokes_mm, center):
    """Shape frame: +u = left, +v = away from the robot, turned to face the drawing spot (a drawing to
    the side of the robot is upright as seen from the robot's base, not tilted by the base angle)."""
    a = math.atan2(center[1], center[0])
    c, s = math.cos(a), math.sin(a)
    return [[(center[0] + (v * c - u * s) / 1000, center[1] + (v * s + u * c) / 1000) for u, v in st] for st in strokes_mm]


def fit_strokes(strokes_mm, size_mm):
    """Centre strokes on 0,0 and scale them so the larger side is size_mm (aspect kept).
    Single points (stray clicks) are dropped first."""
    strokes_mm = [st for st in strokes_mm if len(st) >= 2]
    pts = [p for st in strokes_mm for p in st]
    if len(pts) < 2:
        raise ValueError("the drawing is empty")
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
    k = size_mm / max(max(us) - min(us), max(vs) - min(vs), 1e-6)
    return [[((u - cu) * k, (v - cv) * k) for u, v in st] for st in strokes_mm]


def fit_plane(samples):
    P = np.array(samples)
    A = np.c_[P[:, 0], P[:, 1], np.ones(len(P))]
    return np.linalg.lstsq(A, P[:, 2], rcond=None)[0].tolist()


def plane_z(coef, xy):
    z = coef[0] * xy[0] + coef[1] * xy[1] + coef[2]
    return z + coef[3] * xy[0] * xy[1] if len(coef) > 3 else z


def fit_surface(samples):
    """Paper height from touches: bilinear with 5+ touches (the arm's own height error is not flat
    over a whole sheet), a plane with 3-4, a level sheet with fewer."""
    P = np.array(samples, float)
    if len(P) < 3:
        return [0.0, 0.0, float(P[:, 2].mean())]
    cols = [P[:, 0], P[:, 1], np.ones(len(P))] + ([P[:, 0] * P[:, 1]] if len(P) >= 5 else [])
    return np.linalg.lstsq(np.c_[tuple(cols)], P[:, 2], rcond=None)[0].tolist()


def pen_length(T_straight, T_tilted, pen_dir):
    """Pivot: the tip was on the same spot in both poses -> tool + L*d agree. Least squares for L.
    Returns (L_m, mismatch_m, tilt_deg)."""
    p1, p2 = T_straight[:3, 3], T_tilted[:3, 3]
    d1, d2 = T_straight[:3, :3] @ pen_dir, T_tilted[:3, :3] @ pen_dir
    dd = d1 - d2
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, float(d1 @ d2)))))
    if dd @ dd < 1e-9:
        return PEN_NOMINAL, 1.0, tilt
    L = float(-(p1 - p2) @ dd / (dd @ dd))
    return L, float(np.linalg.norm((p1 + L * d1) - (p2 + L * d2))), tilt


# ------------------------------------------------------------------ dry run (no arm)
def pen_setup(prof):
    """Pen direction, drawing spot, paper height, pen length and the arm pose from the last hand
    placement (measured.yaml), else the profile defaults."""
    m = prof.get("measured", {}).get("pen") or {}
    pen_dir = np.array(m.get("pen_dir_tool") or prof["wrist"]["pen_dir_tool"], float)
    return {"pen_dir": pen_dir / np.linalg.norm(pen_dir),
            "center": np.array(m.get("center") or prof["drawing"]["center"], float),
            "paper_z": float(m.get("paper_z", 0.0)), "length": float(m.get("length", PEN_NOMINAL)),
            "plane": m.get("plane") or [0.0, 0.0, float(m.get("paper_z", 0.0))],
            "angles": m.get("angles"), "placed": "center" in m, "length_measured": bool(m.get("length_measured"))}


def travel_heights(strokes, d):
    """Pen-up height after each stroke (metres, base frame): a short hop when the next stroke starts
    close by (hatching, letters), the full lift for long moves."""
    hop, near = d.get("hop", 0.006), d.get("hop_within", 0.015)
    return [hop if k + 1 < len(strokes) and math.dist(st[-1], strokes[k + 1][0]) <= near else d["lift"]
            for k, st in enumerate(strokes)]


def _search_pose(xyz, pen_dir, length):
    """Coarse grid of seeds -> the most vertical pen pose that reaches xyz (a hand placement may use a
    wrist roll outside the profile's range, so this one has no roll limit)."""
    import itertools
    pan = math.degrees(math.atan2(xyz[1], xyz[0]))
    best = None
    for r, l, e, w in itertools.product((90, 0, -90, 45, -45), (0, -20, 20, -40), (50, 80, 20, -20), (-30, 0, 40, 80)):
        for p in (pan, -pan):
            A, err, vert = K.pen_ik_local(xyz, {"shoulder_pan": p, "shoulder_lift": l, "elbow_flex": e,
                                                "wrist_flex": w, "wrist_roll": r}, pen_dir, length=length)
            if err < 0.002 and vert > 0.96 and (best is None or vert > best[1]):
                best = (A, vert)
                if vert > 0.995:
                    return A
    if best is None:
        raise RuntimeError(f"pen cannot point straight down at {np.round(xyz, 3)}")
    return best[0]


A4_LANDSCAPE_MM = (297.0, 210.0)       # u across (long side), v away from the robot


def lean_map(prof, step_mm=15.0, sheet_mm=A4_LANDSCAPE_MM, margin_mm=12.0):
    """Pen lean (deg) on a grid over the sheet, centred on the hand-placed spot, in the drawing frame
    (u left, v away). 90 = unreachable. Near the base the wrist reaches its stop and the pen leans:
    with the pen length only guessed, a lean moves the tip sideways, so drawings stay where it doesn't."""
    ps = pen_setup(prof)
    c, coef = ps["center"], ps["plane"]
    a = math.atan2(c[1], c[0])
    seed = {k: float(v) for k, v in (ps["angles"] or prof["poses"]["home"]).items() if k in K.J5}
    hu, hv = sheet_mm[0] / 2 - margin_mm, sheet_mm[1] / 2 - margin_mm
    us, vs = np.arange(-hu, hu + 1e-6, step_mm), np.arange(-hv, hv + 1e-6, step_mm)
    if not ps["angles"]:
        seed = _search_pose((*c, plane_z(coef, c)), ps["pen_dir"], ps["length"])
    lean = np.full((len(vs), len(us)), 90.0)
    for i, v in enumerate(vs):
        for j, u in enumerate(us):
            x, y = c[0] + (v * math.cos(a) - u * math.sin(a)) / 1000, c[1] + (v * math.sin(a) + u * math.cos(a)) / 1000
            A, err, vert = K.pen_ik_local((x, y, plane_z(coef, (x, y))), seed, ps["pen_dir"], length=ps["length"])
            if err < 0.002:
                lean[i, j] = math.degrees(math.acos(min(1.0, vert)))
    return {"u": us, "v": vs, "lean": lean, "half": (hu, hv)}


def fit_sheet(strokes_mm, lm, max_size_mm, max_lean_deg=6.0):
    """Largest size (and the shift toward/away from the robot) at which the drawing stays on the sheet
    and where the pen is upright. strokes_mm: any scale, u/v frame. Returns (size_mm, (du, dv))."""
    pts = np.array([p for st in strokes_mm for p in st], float)
    w, h = np.ptp(pts[:, 0]), np.ptp(pts[:, 1])
    U, V = np.meshgrid(lm["u"], lm["v"])
    ok = lm["lean"] <= max_lean_deg
    hu, hv = lm["half"]
    for size in np.arange(max_size_mm, 39.0, -5.0):
        k = size / max(w, h, 1e-9)
        W, H = w * k, h * k
        if W > 2 * hu + 1e-6 or H > 2 * hv + 1e-6:
            continue
        for dv in sorted(np.arange(-hv + H / 2, hv - H / 2 + 1e-6, 5.0), key=abs):
            inside = (np.abs(U) <= W / 2 + 7) & (V >= dv - H / 2 - 7) & (V <= dv + H / 2 + 7)
            if ok[inside].all():
                return float(size), (0.0, float(dv))
    return 40.0, (0.0, 0.0)


def simulate(prof, strokes_mm, step_mm=4.0):
    """Plan a drawing without the arm, the way Drawer.draw_strokes moves: straight pen-up travel,
    straight down, the stroke, straight up. Returns {"frames": [(t_s, angles, pen_down, tip_xyz)],
    "ok": bool, "error": str, "stroke_of_frame": [...]} - planning stops at the first stroke that
    can't be reached, as the real run would (before that stroke's pen goes down). The real run
    plans every 0.2 mm; a few mm is enough to show the motion and check the reach."""
    d, ps = prof["drawing"], pen_setup(prof)
    pen_dir, L, coef = ps["pen_dir"], ps["length"], ps["plane"]
    strokes = to_base(strokes_mm, ps["center"])
    ups = travel_heights(strokes, d)
    step = step_mm / 1000
    A = {k: float(v) for k, v in (ps["angles"] or prof["poses"]["home"]).items() if k in K.J5}
    lo, hi = prof["wrist"]["roll_min_ok"], prof["wrist"]["roll_max_ok"]
    if ps["angles"]:
        lo, hi = min(lo, A["wrist_roll"] - 10), max(hi, A["wrist_roll"] + 10)
    frames, idx, t = [], [], 0.0
    speed, travel = d["speed"], d.get("travel_speed", 0.03)

    def tip(a):
        return K.pen_tip(K.fk(a), pen_dir, L)

    def run(pts, v, down, k):
        nonlocal A, t
        traj = K.pen_path(A, pts, pen_dir, length=L, max_jump=35.0)     # coarse steps: a wrist flip is still caught
        for i, (a, p) in enumerate(zip(traj, pts)):
            t += (np.linalg.norm(np.subtract(p, pts[i - 1])) / v) if i else 0.0
            frames.append((t, a, down, [float(x) for x in p])); idx.append(k)
        A = traj[-1]

    try:
        x, y = strokes[0][0]
        z1 = plane_z(coef, (x, y)) + d["lift"]
        try:
            A = K.pen_ik((x, y, z1), A, pen_dir, roll_limits=(lo, hi), length=L)
        except RuntimeError:
            A = _search_pose((x, y, z1), pen_dir, L)                # no saved hand placement: any vertical pose
        frames.append((0.0, A, False, [float(v) for v in tip(A)])); idx.append(0)
        for k, st in enumerate(strokes):
            pts = [np.array((p[0], p[1], plane_z(coef, p) - d["press"])) for p in K.dense(st, step=step)]
            up = ups[k - 1] if k else d["lift"]
            above = np.array((*st[0], plane_z(coef, st[0]) + up))
            run(K.dense([tip(A), above], step=step * 2), travel, False, k)
            run(K.dense([above, pts[0]], step=0.01), 0.008, False, k)
            run(pts, speed, True, k)
            run(K.dense([pts[-1], np.array((*st[-1], plane_z(coef, st[-1]) + ups[k]))], step=0.01), 0.015, False, k)
        return {"frames": frames, "stroke_of_frame": idx, "ok": True, "error": ""}
    except RuntimeError as e:
        return {"frames": frames, "stroke_of_frame": idx, "ok": False,
                "error": f"stroke {idx[-1] + 1 if idx else 1}: {e}"}


# ------------------------------------------------------------------ session
class Drawer:
    def __init__(self, arm, prof, sess):
        self.arm, self.prof, self.sess = arm, prof, sess
        self.d = prof["drawing"]
        w = prof["wrist"]
        self.roll_lim = (w["roll_min_ok"], w["roll_max_ok"])
        learned = (prof.get("measured", {}).get("pen") or {}).get("pen_dir_tool")
        self.pen_dir = np.array(learned or w["pen_dir_tool"], float)
        self.pen_dir /= np.linalg.norm(self.pen_dir)
        self.center = np.array(self.d["center"], float)
        self.length = 0.0                      # pen length past the tool point (set by touch-off)
        self.coef = None                       # paper plane, in TIP height
        self.samples = []
        self.press, self.speed = self.d["press"], self.d["speed"]

    # -- motion helpers
    def solve(self, xyz, seed=None, tip=False):
        seed = seed or self.arm.angles()
        L = self.length if tip else 0.0
        try:
            return K.pen_ik(xyz, seed, self.pen_dir, roll_limits=self.roll_lim, length=L)
        except RuntimeError:
            # near a hand-placed pose the multi-seed search can miss (or leave the roll range the
            # person put the wrist in); a close single-seed solution from where the arm is will do
            A, err, vert = K.pen_ik_local(xyz, seed, self.pen_dir, length=L)
            if err < 0.004 and vert > 0.9:
                return A
            raise

    def goto(self, xyz, duration=2.0, tip=False):
        A = self.solve(xyz, tip=tip)
        self.arm.move_joints({f"{k}.pos": A[k] for k in K.J5}, duration)

    def line(self, xyz, speed=0.02, tip=False):
        L = self.length if tip else 0.0
        T = K.fk(self.arm.angles())
        start = K.pen_tip(T, self.pen_dir, L)
        pts = K.dense([start, np.asarray(xyz, float)], step=speed / self.d["rate_hz"])
        traj = K.pen_path(self.arm.angles(), pts, self.pen_dir, length=L)
        self.arm.stream(traj, self.d["rate_hz"], check=self.sess.check_stop)

    def lean(self):
        v = -K.pen_down_z(K.fk(self.arm.angles()), self.pen_dir)
        return math.degrees(math.acos(max(-1.0, min(1.0, v))))

    def roll_by(self, deg):
        lo, hi = self.roll_lim
        tgt = max(lo, min(hi, self.arm.angles()["wrist_roll"] + float(deg)))
        self.arm.move_joints({**{f"{k}.pos": v for k, v in self.arm.angles().items() if k != "gripper"},
                              "wrist_roll.pos": tgt}, duration=1.5)
        self.sess.say(f"wrist roll now {self.arm.angles()['wrist_roll']:.0f} deg")

    # -- steps
    def load_pen(self):
        arm, d = self.arm, self.d
        arm.park(keep_roll=True)
        arm.move_xyz((*d["load_xy"], d["load_z"]), duration=3.0)
        arm.move_joints({**{f"{k}.pos": v for k, v in arm.angles().items() if k != "gripper"},
                         "wrist_roll.pos": d["load_roll"]}, duration=1.5)
        arm.move_xyz((*d["load_xy"], d["load_z"]), duration=1.0)
        arm.open()
        while True:
            c = self.sess.ask("put the pen ACROSS the jaws (like a cigarette), deep between the pads, tip out 5-7 cm, "
                              "then 'go' ('roll <deg>' turns the gripper first)", ["go", "roll <deg>"]).split()
            if c and c[0] == "roll" and len(c) == 2:
                self.roll_by(c[1]); continue
            break
        pos = arm.close(torque=self.d["pen_torque"])
        arm.hold()
        self.sess.event("pen_gripped", gripper=round(pos, 1))
        while pos < self.d.get("min_grip", 4.0):
            # jaws (almost) shut: the pen is too thin to be clamped - it wobbles and the gripper overloads
            c = self.sess.ask(f"the jaws closed to {pos:.1f}% - the pen is too THIN to hold firmly. Wrap tape around it where "
                              "the jaws hold it (to ~12 mm thick): 'go' opens the gripper to put it back, 'ignore' draws anyway",
                              ["go", "ignore"])
            if c.strip().lower() == "ignore":
                break
            arm.open()
            self.sess.ask("put the taped pen ACROSS the jaws, deep between the pads, tip out 5-7 cm, then 'go'", ["go"])
            pos = arm.close(torque=self.d["pen_torque"])
            arm.hold()
            self.sess.event("pen_gripped", gripper=round(pos, 1))
        self.sess.ask(f"pen gripped (contact {pos:.1f}). Let go, tug it to check it is firm, then 'go'")

    def tilt(self):
        while True:
            self.goto((*self.center, 0.12), duration=3.0)
            self.sess.event("pen_pose", roll=round(self.arm.angles()["wrist_roll"], 1), model_lean_deg=round(self.lean(), 1))
            c = self.sess.ask(f"pen over ({self.center[0]*1000:.0f}, {self.center[1]*1000:.0f}) mm. Does the TIP point "
                              "STRAIGHT DOWN? 'go' | 'roll <deg>' | 'flip' (cap end is down) | 'nudge <away_mm> <left_mm>'",
                              ["go", "roll <deg>", "flip", "nudge <away_mm> <left_mm>"]).split()
            if c and c[0] == "nudge" and len(c) == 3:
                self.center = self.center + np.array((float(c[1]), float(c[2]))) / 1000; continue
            if c and c[0] == "flip":
                self.arm.move_xyz((*self.d["load_xy"], self.d["load_z"]), duration=3.0)
                self.sess.ask("hold the pen, then 'go' - the gripper opens so you can turn the pen around")
                self.arm.open()
                self.sess.ask("put the pen back with the TIP on the other side, then 'go'")
                self.arm.close(torque=self.d["pen_torque"]); self.arm.hold()
                self.sess.ask("let go of the pen, then 'go'"); continue
            if c and c[0] == "roll" and len(c) == 2:
                self.roll_by(c[1])
                while True:   # hold this pose: re-solving would undo the manual roll
                    c = self.sess.ask("does the TIP point straight down now? 'go' | 'roll <deg>'", ["go", "roll <deg>"]).split()
                    if c and c[0] == "roll" and len(c) == 2:
                        self.roll_by(c[1]); continue
                    break
            break
        R = K.fk(self.arm.angles())[:3, :3]
        self.pen_dir = R.T @ DOWN                    # operator confirmed: this pose = pen straight down
        self.prof.save_measured("pen", {"pen_dir_tool": [round(float(v), 3) for v in self.pen_dir]})
        self.sess.event("pen_dir_learned", pen_dir_tool=[round(float(v), 3) for v in self.pen_dir])

    def jog_touch(self, xy, z_start):
        """Bring the tool to z_start over xy, then let the operator jog until the tip touches."""
        z = z_start
        self.goto((*xy, z), duration=2.5)
        while True:
            c = self.sess.ask(f"at ({xy[0]*1000:.0f}, {xy[1]*1000:.0f}) mm: 'down' (1 mm), 'down 3', 'up', 'up 5' "
                              "(max 5 per step), or 'touch' when the tip just meets the paper",
                              ["down", "down 3", "up", "touch"]).split()
            if not c:
                continue
            if c[0] == "touch":
                break
            if c[0] in ("down", "up"):
                st = min((float(c[1]) if len(c) > 1 else 1.0) / 1000, 0.005)
                z += -st if c[0] == "down" else st
                self.line((*xy, z), speed=0.005)
        tool_z = float(K.fk(self.arm.angles())[2, 3])
        self.line((*xy, tool_z + self.d["lift"]), speed=0.01)
        return tool_z

    def touch_off(self):
        hover = 0.12
        while True:
            c = self.sess.ask("how high is the pen TIP above the paper now, in mm? (e.g. '85')").split()
            try:
                h = float(c[0]) / 1000; break
            except (ValueError, IndexError):
                continue
        tool_z = self.jog_touch(self.center, hover - max(0.0, h - 0.020))
        # paper ~ table level (z ~ 0): pen length along its axis from the tool point
        vz = -K.pen_down_z(K.fk(self.arm.angles()), self.pen_dir)
        self.length = tool_z / max(vz, 0.5)
        self.samples = [(float(self.center[0]), float(self.center[1]), 0.0)]   # tip height 0 at the centre
        self.coef = [0.0, 0.0, 0.0]
        self.sess.event("touch_off", tool_z_mm=round(tool_z * 1000, 1), pen_length_mm=round(self.length * 1000, 1))

    def level(self, size_mm):
        h = size_mm / 2000
        for xy in (self.center + (h, h), self.center + (h, -h), self.center + (-h, h)):
            tool_z = self.jog_touch(xy, plane_z(self.coef, xy) + self.length + 0.006)   # start just above the known paper
            tip = K.pen_tip(K.fk(self.arm.angles()), self.pen_dir, self.length)   # after lifting
            self.samples.append((float(xy[0]), float(xy[1]), float(tool_z - self.length)))
            self.sess.event("corner_touch", xy_mm=[round(v * 1000) for v in xy], tip_z_mm=round((tool_z - self.length) * 1000, 1))
        self.coef = fit_plane(self.samples)
        zs = [p[2] * 1000 for p in self.samples]
        self.sess.event("plane", heights_mm=[round(z, 1) for z in zs],
                        tilt_deg=round(math.degrees(math.atan(math.hypot(self.coef[0], self.coef[1]))), 2))

    def hand_place(self):
        """The person places the pen by hand: tip on the paper, pen straight down. Only the gripper
        keeps torque. The paper is wherever the tip was put: it need not be at z = 0 (on some setups
        the table is 1-2 cm below the base's zero). With the pen vertical the pen length doesn't change
        where the tool goes, so a nominal length is used and the paper plane is set from that pose."""
        arm, bus = self.arm, self.arm.robot.bus
        joints = [m for m in bus.motors if m != "gripper"]
        while True:
            self.sess.ask("HOLD THE ARM (the forearm), then 'go' - the arm goes limp; the gripper keeps the pen", ["go"])
            bus.disable_torque(joints)
            try:
                # the arm's height error is not flat over a whole sheet: touch the corners first, the centre last
                corners = []
                for n in range(1, 5):
                    c = self.sess.ask(f"move the pen by hand: touch the paper near a CORNER of the sheet ({n} of 4, about "
                                      "2 cm in from the edges), pen straight down, then 'go' ('done' skips the corners)",
                                      ["go", "done"])
                    if c.strip().lower() == "done":
                        break
                    corners.append(arm.angles())
                # pen length (tool point -> tip) from two poses with the tip on the same spot: a guessed
                # length puts a leaning pen's tip off by up to cm (corner touches, the near side of the sheet)
                c = self.sess.ask("now put the tip ON the paper at the CENTRE of the sheet with the pen TILTED about 25 deg "
                                  "(lean it toward you or sideways); hold it still, then 'go'", ["go", "skip"])
                tilted = None if c.strip().lower() == "skip" else arm.angles()
                self.sess.ask("keep the tip on the SAME centre spot and stand the pen STRAIGHT DOWN; hold it still, "
                              "then 'go'", ["go"])
                a = arm.angles()
            finally:
                present = bus.sync_read("Present_Position", joints)
                bus.sync_write("Goal_Position", present)          # hold exactly here, no jump
                bus.enable_torque(joints)
            T = K.fk(a)
            pen_dir = T[:3, :3].T @ DOWN                          # this pose = pen straight down
            length, measured = PEN_NOMINAL, False
            if tilted is not None:
                L, resid, tilt = pen_length(T, K.fk(tilted), pen_dir)
                if tilt >= 10 and 0.01 <= L <= 0.15 and resid <= 0.006:
                    length, measured = L, True
                    self.sess.say(f"pen length {L * 1000:.0f} mm (tilt {tilt:.0f} deg, the two tips agree to {resid * 1000:.1f} mm)")
                else:
                    self.sess.say(f"could not measure the pen length (tilt {tilt:.0f} deg, length {L * 1000:.0f} mm, "
                                  f"mismatch {resid * 1000:.1f} mm) - using {PEN_NOMINAL * 1000:.0f} mm")
            tips = [K.pen_tip(T, pen_dir, length)] + [K.pen_tip(K.fk(c), pen_dir, length) for c in corners]
            off = [n for n, t in enumerate(tips[1:], 1) if abs(t[2] - tips[0][2]) > 0.03]
            if off:                                               # > 3 cm from the centre: not on the paper
                self.sess.say(f"ignoring corner touch {', '.join(map(str, off))} (more than 3 cm off the centre height)")
            all_heights = [round((t[2] - tips[0][2]) * 1000, 1) for t in tips[1:]]
            tips = [t for n, t in enumerate(tips) if n not in off]
            tool_z = float(T[2, 3])
            center = np.array(T[:2, 3], float)
            # (no reachability gate here: the person just put the arm in this pose. A shape that can't be
            # drawn there fails when it is planned - before the pen goes down - and says why.)
            break
        paper_z = float(tips[0][2])                               # tip height at the placed point
        self.pen_dir, self.center, self.length = pen_dir, center, length
        self.samples = [tuple(float(v) for v in t) for t in tips]
        self.coef = fit_surface(self.samples)
        self.prof.save_measured("pen", {"pen_dir_tool": [round(float(v), 3) for v in self.pen_dir],
                                        "center": [round(float(v), 4) for v in center],
                                        "paper_z": round(paper_z, 4), "length": round(length, 4),
                                        "length_measured": measured,
                                        "plane": [round(float(v), 6) for v in self.coef],
                                        "touches": [[round(v, 4) for v in t] for t in self.samples],
                                        "angles": {k: round(float(v), 1) for k, v in a.items()}})
        self.sess.event("hand_placed", center_mm=[round(v * 1000) for v in center], tool_z_mm=round(tool_z * 1000, 1),
                        touches=len(tips), corner_heights_mm=all_heights, pen_length_mm=round(length * 1000, 1),
                        length_measured=measured)
        roll = a["wrist_roll"]                                    # allow the roll the person chose
        self.roll_lim = (min(self.roll_lim[0], roll - 10), max(self.roll_lim[1], roll + 10))
        self.sess.ask("got it - you can let go of the arm, then 'go' (the pen lifts off the paper)", ["go"])
        self.goto((*center, tool_z + self.d["lift"]), duration=1.0)

    def draw(self, name, size_mm):
        size_mm = min(size_mm, self.d["max_size_mm"])
        self.draw_strokes(shape(name, size_mm), name)

    def draw_file(self, fname, size_mm):
        f = self.prof.run_dir / "drawings" / f"{''.join(ch for ch in fname if ch.isalnum() or ch in '-_')}.json"
        if not f.exists():
            raise FileNotFoundError(f"no drawing '{fname}' in {f.parent}")
        d = json.loads(f.read_text())
        size_mm = min(float(d.get("size_mm", size_mm)), self.d["max_size_mm"])
        du, dv = d.get("offset_mm", (0.0, 0.0))                  # from the dashboard's "fill the sheet"
        strokes = [[(u + du, v + dv) for u, v in P.simplify(st, 0.15)] for st in fit_strokes(d["strokes"], size_mm)]
        self.draw_strokes(strokes, fname)

    def draw_strokes(self, strokes_mm, name):
        strokes = to_base(strokes_mm, self.center)
        pts_mm = [p for st in strokes_mm for p in st]
        size_mm = max(max(p[0] for p in pts_mm) - min(p[0] for p in pts_mm), max(p[1] for p in pts_mm) - min(p[1] for p in pts_mm))
        self.sess.event("drawing", shape=name, size_mm=round(size_mm), strokes=len(strokes),
                        press_mm=self.press * 1000, speed_mm_s=self.speed * 1000)
        self.arm.set_grip_torque(self.d["pen_torque"])
        ups = travel_heights(strokes, self.d)
        t0, total = time.time(), 0.0
        try:
            for k, st in enumerate(strokes):
                pts = K.dense(st, step=self.speed / self.d["rate_hz"])
                path = [(p[0], p[1], plane_z(self.coef, p) - self.press) for p in pts]
                self.sess.say(f"planning stroke {k + 1}/{len(strokes)} ({len(pts)} points)...")
                A0 = self.solve(path[0], tip=True)                      # plan BEFORE the pen goes down
                traj = K.pen_path(A0, path, self.pen_dir, length=self.length)
                self.sess.event("stroke_start", index=k + 1, of=len(strokes))
                # travel: straight lines with the pen vertical - across at lift height, then down.
                # (A joint-space move between two lifted poses swings in an arc and can dip the tip
                # onto the paper on the way: that drew streaks between strokes.)
                up = ups[k - 1] if k else self.d["lift"]
                self.line((*pts[0], plane_z(self.coef, pts[0]) + up), speed=self.d.get("travel_speed", 0.03), tip=True)
                self.line(path[0], speed=0.008, tip=True)
                self.arm.stream(traj, self.d["rate_hz"], check=self.sess.check_stop)
                self.line((*pts[-1], plane_z(self.coef, pts[-1]) + ups[k]), speed=0.015, tip=True)   # straight up
                total += sum(np.linalg.norm(np.subtract(pts[i + 1], pts[i])) for i in range(len(pts) - 1))
                self.sess.event("stroke_done", index=k + 1, of=len(strokes))
        finally:
            self.arm.hold()
        self.sess.event("drawn", shape=name, line_mm=round(total * 1000), seconds=round(time.time() - t0))

    def lift_clear(self):
        try:
            p = K.fk(self.arm.angles())[:3, 3]
            self.goto((p[0], p[1], p[2] + 0.04), duration=1.0)
        except RuntimeError:
            pass

    def give_back(self):
        self.goto((*self.center, 0.12), duration=2.0)
        self.arm.move_xyz((*self.d["load_xy"], self.d["load_z"]), duration=3.0)
        self.sess.ask("hold the pen, then 'go' to release it")
        self.arm.open()
        self.arm.set_grip_torque(self.prof["gripper"]["torque"])


def main(a, prof, sess):
    from ..hardware.arm import Arm
    from ..session import Stopped
    with Arm(prof) as arm:
        dr = Drawer(arm, prof, sess)
        dr.load_pen()
        dr.hand_place()
        while True:
            try:
                dr.goto((*dr.center, plane_z(dr.coef, dr.center) + dr.length + 0.04), duration=1.5)
            except RuntimeError as e:                              # stay at the menu instead of crashing
                sess.event("error", message=str(e)); sess.say(f"ERROR {e}")
            c = sess.ask("'draw <shape> <size_mm>' (" + " ".join(SHAPES) + "), 'drawfile <name> [size_mm]', 'level <size_mm>', "
                         "'press <mm>', 'speed <mm/s>', 'move <away_mm> <left_mm>', 'place', 'probe', 'quit'",
                         ["draw <shape> <size_mm>", "drawfile <name>", "level 60", "press <mm>", "speed <mm/s>",
                          "move <away> <left>", "place", "probe", "quit"]).split()
            if not c:
                continue
            try:
                if c[0] == "quit":
                    break
                elif c[0] == "draw" and len(c) >= 2:
                    dr.draw(c[1], float(c[2]) if len(c) > 2 else 40.0)
                elif c[0] == "drawfile" and len(c) >= 2:
                    dr.draw_file(c[1], float(c[2]) if len(c) > 2 else 50.0)
                elif c[0] == "place":
                    dr.hand_place()
                elif c[0] == "level":
                    dr.level(float(c[1]) if len(c) > 1 else 60.0)
                elif c[0] == "press" and len(c) == 2:
                    dr.press = min(float(c[1]), 5.0) / 1000; sess.say(f"press {dr.press*1000:.1f} mm")
                elif c[0] == "speed" and len(c) == 2:
                    dr.speed = min(max(float(c[1]), 3.0), 30.0) / 1000; sess.say(f"speed {dr.speed*1000:.0f} mm/s")
                elif c[0] == "move" and len(c) == 3:
                    new = dr.center + np.array((float(c[1]), float(c[2]))) / 1000
                    dr.solve((*new, plane_z(dr.coef, new) + 0.04), tip=True)       # raises if unreachable
                    dr.center = new; sess.say(f"centre ({new[0]*1000:.0f}, {new[1]*1000:.0f}) mm - 'probe' re-measures here")
                elif c[0] == "probe":
                    dr.touch_off()
                else:
                    sess.say("unknown command")
            except Stopped:
                dr.lift_clear(); raise
            except Exception as e:
                sess.event("error", message=str(e)); sess.say(f"ERROR {e}")
                dr.lift_clear()
        dr.give_back()
