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
    return coef[0] * xy[0] + coef[1] * xy[1] + coef[2]


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
        return K.pen_ik(xyz, seed or self.arm.angles(), self.pen_dir, roll_limits=self.roll_lim,
                        length=self.length if tip else 0.0)

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
                self.sess.ask("move the pen by hand: tip ON the paper where the drawing should be, pen STRAIGHT DOWN; "
                              "hold it still, then 'go'", ["go"])
                a = arm.angles()
            finally:
                present = bus.sync_read("Present_Position", joints)
                bus.sync_write("Goal_Position", present)          # hold exactly here, no jump
                bus.enable_torque(joints)
            T = K.fk(a)
            tool_z = float(T[2, 3])
            pen_dir = T[:3, :3].T @ DOWN                          # this pose = pen straight down
            center = np.array(T[:2, 3], float)
            try:
                K.pen_ik((*center, tool_z + 0.04), arm.angles(), pen_dir, roll_limits=self.roll_lim)
            except Exception:
                self.sess.say("the pen can't stay straight down there - place it a little closer to or further "
                              "from the robot (about 24-33 cm from the base), then try again")
                continue
            break
        paper_z = tool_z - PEN_NOMINAL                            # tip height at the placed point
        self.pen_dir, self.center, self.length = pen_dir, center, PEN_NOMINAL
        self.samples, self.coef = [(float(center[0]), float(center[1]), paper_z)], [0.0, 0.0, paper_z]
        self.prof.save_measured("pen", {"pen_dir_tool": [round(float(v), 3) for v in self.pen_dir]})
        self.sess.event("hand_placed", center_mm=[round(v * 1000) for v in center], tool_z_mm=round(tool_z * 1000, 1))
        self.sess.ask("got it - you can let go of the arm, then 'go' (the pen lifts off the paper)", ["go"])
        self.line((*center, tool_z + self.d["lift"]), speed=0.01)

    def draw(self, name, size_mm):
        size_mm = min(size_mm, self.d["max_size_mm"])
        self.draw_strokes(shape(name, size_mm), name)

    def draw_file(self, fname, size_mm):
        f = self.prof.run_dir / "drawings" / f"{''.join(ch for ch in fname if ch.isalnum() or ch in '-_')}.json"
        if not f.exists():
            raise FileNotFoundError(f"no drawing '{fname}' in {f.parent}")
        d = json.loads(f.read_text())
        size_mm = min(float(d.get("size_mm", size_mm)), self.d["max_size_mm"])
        self.draw_strokes(fit_strokes(d["strokes"], size_mm), fname)

    def draw_strokes(self, strokes_mm, name):
        strokes = to_base(strokes_mm, self.center)
        pts_mm = [p for st in strokes_mm for p in st]
        size_mm = max(max(p[0] for p in pts_mm) - min(p[0] for p in pts_mm), max(p[1] for p in pts_mm) - min(p[1] for p in pts_mm))
        self.sess.event("drawing", shape=name, size_mm=round(size_mm), press_mm=self.press * 1000, speed_mm_s=self.speed * 1000)
        self.arm.set_grip_torque(self.d["pen_torque"])
        t0, total = time.time(), 0.0
        try:
            for k, st in enumerate(strokes):
                pts = K.dense(st, step=self.speed / self.d["rate_hz"])
                path = [(p[0], p[1], plane_z(self.coef, p) - self.press) for p in pts]
                self.sess.say(f"planning stroke {k + 1}/{len(strokes)} ({len(pts)} points)...")
                A0 = self.solve(path[0], tip=True)                      # plan BEFORE the pen goes down
                traj = K.pen_path(A0, path, self.pen_dir, length=self.length)
                self.goto((*pts[0], plane_z(self.coef, pts[0]) + self.d["lift"]), duration=1.2, tip=True)
                self.line(path[0], speed=0.01, tip=True)
                self.arm.stream(traj, self.d["rate_hz"], check=self.sess.check_stop)
                self.line((*pts[-1], plane_z(self.coef, pts[-1]) + self.d["lift"]), speed=0.02, tip=True)
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
            dr.goto((*dr.center, plane_z(dr.coef, dr.center) + dr.length + 0.04), duration=1.5)
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
