"""Motion library: built-in moves (hello, scare, dance) and the operator's recordings.

A motion is a list of timed joint keyframes, all joints, lerobot degrees (gripper 0-100):

    {"name": "hello", "builtin": true, "interp": "smooth" | "linear",
     "joints": [...6 names...], "t": [s, ...], "q": [[6 values], ...]}

"smooth" eases in and out of every keyframe (sparse, hand-written moves); "linear" joins the
samples with straight lines (dense data: recordings and the generated dance).

Recordings are saved as JSON in the dashboard's motions/ folder (or $SO101_MOTIONS_DIR), so a good
one can be committed and replayed on another PC.

Every motion is checked before it plays (check()): joints inside the limits and no joint faster
than MAX_JOINT_SPEED at the chosen playback speed; built-ins must also keep every link of the arm
at least MIN_LINK_Z above the table (recordings repeat what the arm physically did, which may touch it). The built-ins are designed to start from and end at the upright "stand" pose.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
from pathlib import Path

import numpy as np

from so101 import kinematics as K
from .frames import link_frames

JOINTS = list(K.JOINT_LIMITS)
MIN_LINK_Z = 0.04            # m: wrist, gripper and jaws stay this far above the table
MAX_JOINT_SPEED = 300.0      # deg/s at the chosen playback speed (the 5 V servos top out near this)
RECORD_HZ = 25
CHECK_LINKS = ("wrist_link", "gripper_link", "gripper_frame_link", "moving_jaw_so101_v1_link")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "motions"

# poses the built-ins are made of (checked with FK: every link well above the table)
STAND = {"shoulder_pan": 0, "shoulder_lift": -95, "elbow_flex": 45, "wrist_flex": -35, "wrist_roll": 0, "gripper": 0}
RAISED = {"shoulder_pan": 0, "shoulder_lift": -65, "elbow_flex": 5, "wrist_flex": -25, "wrist_roll": 0, "gripper": 55}
CROUCH = {"shoulder_pan": 0, "shoulder_lift": -90, "elbow_flex": 95, "wrist_flex": 0, "wrist_roll": 0, "gripper": 0}
LUNGE = {"shoulder_pan": 0, "shoulder_lift": -25, "elbow_flex": -5, "wrist_flex": -25, "wrist_roll": 0, "gripper": 90}


def _pose(base, **kw):
    return {**base, **kw}


def _from_keys(name, keys, interp="smooth", description=""):
    """keys: [(duration_from_previous_s, pose dict), ...]; the first entry's duration is ignored."""
    t, q, now = [], [], 0.0
    for i, (dt, pose) in enumerate(keys):
        now += 0.0 if i == 0 else dt
        t.append(round(now, 3))
        q.append([float(pose[j]) for j in JOINTS])
    return {"name": name, "builtin": True, "interp": interp, "description": description,
            "joints": JOINTS, "t": t, "q": q}


# ------------------------------------------------------------------ built-ins
def hello():
    """Raise the 'hand', wave three times by rolling the wrist, two little 'claps', back to stand."""
    keys = [(0, STAND), (1.2, RAISED)]
    for _ in range(3):
        keys += [(0.45, _pose(RAISED, wrist_roll=-35, wrist_flex=-35)),
                 (0.45, _pose(RAISED, wrist_roll=35, wrist_flex=-15))]
    keys += [(0.4, RAISED),
             (0.2, _pose(RAISED, gripper=10)), (0.2, RAISED),
             (0.2, _pose(RAISED, gripper=10)), (0.25, RAISED),
             (1.2, STAND)]
    return _from_keys("hello", keys, description="raises its hand and waves")


def scare():
    """Hide low and still, peek, then rear up and snap the jaws (fast), then calm down."""
    keys = [(0, STAND), (1.4, CROUCH),
            (0.8, _pose(CROUCH, shoulder_pan=-15)), (0.8, _pose(CROUCH, shoulder_pan=15)), (0.6, CROUCH),
            (1.0, _pose(CROUCH, wrist_flex=-10)),            # holds its breath...
            (0.55, LUNGE),                                    # BOO
            (0.15, _pose(LUNGE, gripper=5)), (0.15, LUNGE),
            (0.15, _pose(LUNGE, gripper=5)), (0.15, LUNGE),
            (0.9, LUNGE),
            (1.6, STAND)]
    return _from_keys("scare", keys, description="hides low, then jumps up snapping its jaws")


def dance(seconds=12.0, bpm=100.0):
    """Sway, bob, twist and clap to a beat; amplitude fades in and out."""
    beat = 60.0 / bpm
    centre = {"shoulder_pan": 0, "shoulder_lift": -72, "elbow_flex": 35, "wrist_flex": -25, "wrist_roll": 0, "gripper": 30}
    lead = 1.5
    t_all = np.arange(0.0, seconds + 1e-9, 0.05)
    t, q = [0.0], [[float(STAND[j]) for j in JOINTS]]
    for tt in t_all:
        a = min(1.0, tt / 1.5, (seconds - tt) / 1.5)          # fade in / out
        a = max(0.0, a)
        w = 2 * math.pi / beat
        p = {
            "shoulder_pan": centre["shoulder_pan"] + a * 30 * math.sin(w * tt / 4),
            "shoulder_lift": centre["shoulder_lift"] + a * 8 * math.sin(w * tt),
            "elbow_flex": centre["elbow_flex"] - a * 10 * math.sin(w * tt),
            "wrist_flex": centre["wrist_flex"] + a * 22 * math.sin(w * tt / 2),
            "wrist_roll": centre["wrist_roll"] + a * 40 * math.sin(w * tt / 2 + math.pi / 2),
            "gripper": centre["gripper"] + a * 30 * math.cos(w * tt),
        }
        t.append(round(lead + tt, 3))
        q.append([round(p[j], 2) for j in JOINTS])
    t.append(round(t[-1] + 1.5, 3))
    q.append([float(STAND[j]) for j in JOINTS])
    m = {"name": "dance", "builtin": True, "interp": "linear", "description": "sways, bobs and claps to a beat",
         "joints": JOINTS, "t": t, "q": q}
    # the lead-in and lead-out legs are long straight lines: ease them like keyframes
    m["ease_ends"] = True
    return m


BUILTINS = {"hello": hello, "scare": scare, "dance": dance}


# ------------------------------------------------------------------ sampling
def duration(m):
    return float(m["t"][-1])


def sample(m, t):
    """Joint dict at time t (s) of the motion."""
    T, Q = m["t"], m["q"]
    if t <= T[0]:
        row = Q[0]
    elif t >= T[-1]:
        row = Q[-1]
    else:
        i = int(np.searchsorted(T, t, side="right")) - 1
        u = (t - T[i]) / max(T[i + 1] - T[i], 1e-9)
        ends = m.get("ease_ends") and (i == 0 or i == len(T) - 2)
        if m.get("interp") == "smooth" or ends:
            u = 0.5 - 0.5 * math.cos(math.pi * u)
        row = [a + (b - a) * u for a, b in zip(Q[i], Q[i + 1])]
    return dict(zip(m["joints"], (float(v) for v in row)))


def check(m, limits, speed=1.0):
    """Problems that make the motion unsafe at this playback speed ([] = fine), plus stats."""
    problems = []
    dt = 0.02
    n = int(duration(m) / dt) + 1
    samples = [sample(m, i * dt) for i in range(n + 1)]
    for j in JOINTS:
        lo, hi = limits[j]
        vals = [s[j] for s in samples]
        if min(vals) < lo - 0.5 or max(vals) > hi + 0.5:
            problems.append(f"{j} goes to {min(vals):.0f}..{max(vals):.0f}, outside its limits {lo:.0f}..{hi:.0f}")
    vmax, vj = 0.0, ""
    for a, b in zip(samples, samples[1:]):
        for j in JOINTS[:5]:                                   # gripper speed is not a safety issue
            v = abs(b[j] - a[j]) / dt * speed
            if v > vmax:
                vmax, vj = v, j
    if vmax > MAX_JOINT_SPEED:
        problems.append(f"too fast at {speed:g}x: {vj} would move at {vmax:.0f} deg/s (max {MAX_JOINT_SPEED:.0f})"
                        " - choose a slower playback speed")
    zmin = min(min(link_frames(s)[k][2, 3] for k in CHECK_LINKS) for s in samples[::5])
    # table clearance is a design rule for the built-ins; a recording is what the arm physically did
    # (it may rest on or touch the table, e.g. from `home` or while picking), so it is reported only
    if m.get("builtin") and zmin < MIN_LINK_Z:
        problems.append(f"the arm would come within {zmin * 1000:.0f} mm of the table (min {MIN_LINK_Z * 1000:.0f} mm)")
    return problems, {"max_joint_speed": round(vmax, 1), "min_link_z_mm": round(float(zmin) * 1000, 1),
                      "duration_s": round(duration(m) / speed, 2)}


def frames_for_preview(m, speed=1.0, rate_hz=20):
    """Link poses over time for the twin's ghost preview (column-major 4x4 per link)."""
    out = []
    n = int(duration(m) / speed * rate_hz) + 1
    for i in range(n + 1):
        F = link_frames(sample(m, min(duration(m), i / rate_hz * speed)))
        out.append({k: [round(float(x), 4) for x in T.T.ravel()] for k, T in F.items()})
    return {"rate_hz": rate_hz, "frames": out}


# ------------------------------------------------------------------ recordings
def from_recording(samples, name):
    """samples: [(t_s, {joint: deg}), ...] at the control rate -> a motion. Trims the still start
    and end (so replay doesn't sit waiting) and resamples to RECORD_HZ."""
    if len(samples) < 2:
        raise ValueError("nothing was recorded")
    Q = np.array([[s[1][j] for j in JOINTS] for s in samples])
    T = np.array([s[0] for s in samples])
    moving = np.where(np.abs(Q - Q[0]).max(axis=1) > 1.0)[0]
    moving_end = np.where(np.abs(Q - Q[-1]).max(axis=1) > 0.2)[0]       # keep the settling tail
    if len(moving) == 0:
        raise ValueError("the arm did not move during the recording")
    i0 = max(0, moving[0] - 5)
    i1 = min(len(T) - 1, (moving_end[-1] if len(moving_end) else len(T) - 1) + 5)
    T, Q = T[i0:i1 + 1] - T[i0], Q[i0:i1 + 1]
    grid = np.arange(0.0, T[-1] + 1e-9, 1.0 / RECORD_HZ)
    Qg = np.stack([np.interp(grid, T, Q[:, k]) for k in range(len(JOINTS))], axis=1)
    return {"name": name, "builtin": False, "interp": "linear", "joints": JOINTS,
            "created": _dt.datetime.now().isoformat(timespec="seconds"), "source": "recorded",
            "t": [round(float(x), 3) for x in grid], "q": [[round(float(v), 1) for v in row] for row in Qg]}


class Library:
    def __init__(self, folder=None):
        self.folder = Path(folder or os.environ.get("SO101_MOTIONS_DIR") or DEFAULT_DIR)

    def _file(self, name):
        if not NAME_RE.match(name or ""):
            raise ValueError("names: 1-40 letters, digits, spaces, - or _ (start with a letter or digit)")
        return self.folder / (re.sub(r"\s+", "_", name.strip()).lower() + ".json")

    def list(self):
        out = [{"name": k, "builtin": True, "description": f().get("description", ""),
                "duration_s": round(duration(f()), 1)} for k, f in BUILTINS.items()]
        if self.folder.exists():
            for f in sorted(self.folder.glob("*.json")):
                try:
                    m = json.loads(f.read_text(encoding="utf-8"))
                    out.append({"name": m["name"], "builtin": False, "created": m.get("created"),
                                "duration_s": round(duration(m), 1), "description": "recorded"})
                except Exception:
                    continue
        return out

    def get(self, name):
        if name in BUILTINS:
            return BUILTINS[name]()
        f = self._file(name)
        if not f.exists():
            raise ValueError(f"no motion called '{name}'")
        return json.loads(f.read_text(encoding="utf-8"))

    def save(self, m, overwrite=False):
        if m["name"] in BUILTINS:
            raise ValueError(f"'{m['name']}' is a built-in motion - choose another name")
        f = self._file(m["name"])
        if f.exists() and not overwrite:
            raise ValueError(f"a motion called '{m['name']}' already exists")
        self.folder.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(m), encoding="utf-8")
        return f

    def rename(self, name, new):
        if name in BUILTINS:
            raise ValueError("built-in motions cannot be renamed")
        m = self.get(name)
        old = self._file(name)
        m["name"] = new
        self.save(m)
        old.unlink()

    def delete(self, name):
        if name in BUILTINS:
            raise ValueError("built-in motions cannot be deleted")
        f = self._file(name)
        if not f.exists():
            raise ValueError(f"no motion called '{name}'")
        f.unlink()
