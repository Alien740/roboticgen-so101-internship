"""The dashboard's single owner of the arm: one control loop, every input goes through it.

Inputs (any thread, e.g. HTTP handlers) only change small pieces of state under a lock;
the loop thread is the only code that touches the backend (the serial bus is not thread-safe).

Each tick (RATE_HZ):
    measured <- backend.read()
    target   <- active input: trajectory (pose buttons) | jog velocity (keys, gamepad,
                Cartesian pad) | absolute value (sliders)
    goal     <- target, rate-limited by the speed preset and clamped to joint limits
    backend.write(goal)

Safety:
- E-STOP freezes the goal where the arm IS (not where it was going) and latches until reset.
- jog input expires after JOG_TIMEOUT_S, so a lost browser tab, unplugged gamepad or stuck
  key cannot keep the arm moving.
- wrist roll is limited to the profile's measured free range (it jams near +80).
- gripper stall guard: holding goal 0 on an object is an endless stall that trips the servo's
  overload protection (power cycle needed), so after a stall the goal is eased to just past contact.
- Cartesian jog refuses to go below the table or beyond reach instead of guessing.
"""
from __future__ import annotations

import collections
import math
import threading
import time
import traceback

import numpy as np

from so101 import kinematics as K
from . import backends
from .frames import link_frames

RATE_HZ = 50
JOG_TIMEOUT_S = 0.3
SPEEDS = {   # joint deg/s, Cartesian m/s, gripper units/s
    "slow": {"joint": 15.0, "cart": 0.02, "grip": 40.0},
    "normal": {"joint": 35.0, "cart": 0.05, "grip": 80.0},
    "fast": {"joint": 70.0, "cart": 0.09, "grip": 150.0},
}
BOOST = 2.0                      # Shift / gamepad Y
TABLE_Z = 0.005                  # Cartesian jog keeps the tool point this far above the table
STALL_S = 1.0
HAND_SPEEDUP = 3.0               # hand following uses the preset joint speed x this (it is pre-smoothed)
HAND_TIMEOUT_S = 0.5             # no hand frame for this long -> the arm holds where it is
HAND_BOX = {"r": (0.12, 0.33), "z": (0.02, 0.30), "pitch": (-90.0, 30.0), "pan": (-90.0, 90.0)}
HAND_MAX_ERR = 0.015             # IK further than this from the hand target -> keep the last pose


def joint_limits(prof):
    lim = dict(K.JOINT_LIMITS)
    w = prof.get("wrist", {})
    lim["wrist_roll"] = (float(w.get("roll_min_ok", -180.0)), float(w.get("roll_max_ok", 180.0)))
    return lim


def cart_step(angles, dxyz, pitch_hold, iters=4):
    """Move the tool point by dxyz (m) keeping its pitch, via damped least squares on the 4 arm
    joints (mm and degrees, so both terms weigh about the same). Returns new angles or None."""
    a = dict(angles)
    q = np.array([a[j] for j in K.ARM], float)

    def f(q):
        T = K.fk({**a, **dict(zip(K.ARM, q))})
        return np.r_[T[:3, 3] * 1000.0, K.pitch_deg(T)]

    want = f(q)[:3] + np.asarray(dxyz, float) * 1000.0
    want = np.r_[want, pitch_hold]
    for _ in range(iters):
        y = f(q)
        e = want - y
        if np.abs(e[:3]).max() < 0.05:
            break
        J = np.empty((4, 4))
        for i in range(4):
            dq = np.zeros(4); dq[i] = 0.05
            J[:, i] = (f(q + dq) - y) / 0.05
        q = q + np.linalg.solve(J.T @ J + 0.05 * np.eye(4), J.T @ e)
    if np.linalg.norm(want[:3] - f(q)[:3]) > 1.0:          # > 1 mm off: singular / out of reach
        return None
    return {**a, **dict(zip(K.ARM, q))}


class Controller:
    def __init__(self, prof):
        self.prof = prof
        self.limits = joint_limits(prof)
        reach = prof["motion"].get("drop_reach_max", 0.37)
        self.max_radius = float(reach) + 0.03
        self.lock = threading.Lock()
        self.backend = None
        self.state = "disconnected"          # disconnected | connecting | ready | parking | error
        self.error = ""
        self.estop = False
        self.speed = "normal"
        self.torque = True
        self.owner = "idle"
        self.measured = {}
        self.goal = {}
        self.target = {}
        self.traj = None                     # {start, end, t0, dur, name, then}
        self.jog = {"joints": {}, "cart": {}, "boost": False, "t": 0.0, "source": ""}
        self._pitch_hold = None
        self._stall = {"since": None, "pos": None}
        self._hand = {"t": 0.0, "seed": None, "grip_floor": None}
        self._ops = collections.deque()
        self.warnings = collections.deque(maxlen=20)
        self.rate = 0.0
        self._frames_cache = (None, None)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="so101-control", daemon=True)

    # ================================================================ lifecycle
    def start(self):
        self.thread.start()
        return self

    def shutdown(self, park=True):
        if self.backend is not None and park:
            self.disconnect()
            t0 = time.monotonic()
            while self.backend is not None and time.monotonic() - t0 < 15:
                time.sleep(0.05)
        self._stop.set()
        self.thread.join(timeout=2)

    def warn(self, msg):
        with self.lock:
            if not self.warnings or self.warnings[-1]["msg"] != msg:
                self.warnings.append({"t": time.time(), "msg": msg})

    # ================================================================ commands (any thread)
    def connect(self, kind):
        if kind not in ("sim", "real"):
            raise ValueError("mode must be 'sim' or 'real'")
        with self.lock:
            if self.backend is not None or self.state == "connecting":
                raise RuntimeError("already connected - disconnect first")
            self.state, self.error = "connecting", ""
            self._ops.append(("connect", kind))

    def disconnect(self):
        """Park at the profile's home pose (folded, rests on the table), then release torque."""
        with self.lock:
            if self.backend is None:
                return
            if not self.torque or self.estop or self.state == "error":
                self._ops.append(("close", None))
                return
            self.state = "parking"
            self._start_traj(self._pose("home"), "park", then="close")

    def emergency_stop(self):
        with self.lock:
            self.estop = True
            self.traj = None
            self.jog["joints"], self.jog["cart"] = {}, {}
            self.owner = "E-STOP"
            if self.state == "parking":
                self.state = "ready"
            if self.measured:
                self.goal = dict(self.measured)
                self.target = dict(self.measured)

    def reset_estop(self):
        with self.lock:
            self.estop = False
            self.owner = "idle"
            if self.measured:
                self.goal = dict(self.measured)
                self.target = dict(self.measured)

    def set_speed(self, level):
        if level not in SPEEDS:
            raise ValueError(f"speed must be one of {list(SPEEDS)}")
        with self.lock:
            self.speed = level

    def set_torque(self, on):
        with self.lock:
            self._require_ready()
            self._ops.append(("torque", bool(on)))

    def jog_input(self, joints=None, cart=None, boost=False, source="manual"):
        """Velocity command, each value -1..1 (scaled by the speed preset). Must be repeated
        at least every JOG_TIMEOUT_S while held; send all zeros to stop at once."""
        with self.lock:
            self._require_ready(motion=True)
            joints = {k: max(-1.0, min(1.0, float(v))) for k, v in (joints or {}).items()
                      if k in self.limits and abs(float(v)) > 1e-3}
            cart = {k: max(-1.0, min(1.0, float(v))) for k, v in (cart or {}).items()
                    if k in ("x", "y", "z") and abs(float(v)) > 1e-3}
            if not cart:
                self._pitch_hold = None
            if joints or cart:
                self.traj = None
                self.owner = f"manual:{source}"
            self.jog.update(joints=joints, cart=cart, boost=bool(boost), t=time.monotonic(), source=source)

    def set_joints(self, values, source="slider"):
        """Absolute targets (deg / gripper 0-100); the arm moves there at the preset speed."""
        with self.lock:
            self._require_ready(motion=True)
            self.traj = None
            for k, v in values.items():
                if k in self.limits:
                    self.target[k] = self._clamp(k, float(v))
            self.owner = f"manual:{source}"

    def hand_input(self, pan, r, z, pitch, roll=None, grip=None):
        """Follow a tracked hand: tool point at (pan deg, radius m, height m) with the given pitch,
        plus wrist roll (deg) and gripper (0-100). Values are clamped to a safe box; the IK runs
        here (caller's thread, ~8 ms) so the control loop stays short. Send at ~10-30 Hz; when the
        frames stop the arm simply holds its last target. Returns the clamped target."""
        clip = lambda v, k: max(HAND_BOX[k][0], min(HAND_BOX[k][1], float(v)))
        pan, r, z, pitch = clip(pan, "pan"), clip(r, "r"), clip(z, "z"), clip(pitch, "pitch")
        with self.lock:
            self._require_ready(motion=True)
            if time.monotonic() - self._hand["t"] > HAND_TIMEOUT_S or self._hand["seed"] is None:
                self._hand["seed"] = dict(self.goal)         # (re)engaging: start from where the arm is
            seed = dict(self._hand["seed"])
        xyz = [r * math.cos(math.radians(pan)), r * math.sin(math.radians(pan)), z]
        sol, err = K.ik_local(xyz, seed, pitch)
        ok = err < HAND_MAX_ERR and all(self.limits[j][0] <= sol[j] <= self.limits[j][1] for j in K.ARM)
        with self.lock:
            self._require_ready(motion=True)
            self.traj = None
            self.jog["joints"], self.jog["cart"] = {}, {}
            self._hand["t"] = time.monotonic()
            if ok:
                self._hand["seed"] = sol
                for j in K.ARM:
                    self.target[j] = self._clamp(j, sol[j])
            else:
                self._note("hand is outside the arm's reach - holding the last pose")
            if roll is not None:
                self.target["wrist_roll"] = self._clamp("wrist_roll", float(roll))
            if grip is not None:
                g = self._clamp("gripper", float(grip))
                floor = self._hand["grip_floor"]
                if floor is not None:                         # stall guard eased a grip: don't re-squeeze
                    if g > floor + 10.0:
                        self._hand["grip_floor"] = None
                    else:
                        g = max(g, floor)
                self.target["gripper"] = g
            self.owner = "hand"
        return {"pan": pan, "r": r, "z": z, "pitch": pitch, "reachable": ok}

    def goto(self, pose, duration=None):
        """Smooth move to a profile pose name or a {joint: deg} dict (joints left out stay)."""
        with self.lock:
            self._require_ready(motion=True)
            end = self._pose(pose) if isinstance(pose, str) else {k: float(v) for k, v in pose.items()}
            self._start_traj(end, pose if isinstance(pose, str) else "pose", duration=duration)

    # ================================================================ state for the UI
    def snapshot(self, frames=True):
        with self.lock:
            s = {
                "state": self.state, "mode": getattr(self.backend, "kind", None), "error": self.error,
                "port": getattr(self.backend, "port", None), "estop": self.estop, "speed": self.speed,
                "torque": self.torque, "owner": self.owner, "rate_hz": round(self.rate, 1),
                "measured": {k: round(v, 2) for k, v in self.measured.items()},
                "goal": {k: round(v, 2) for k, v in self.goal.items()},
                "target": {k: round(v, 2) for k, v in self.target.items()},
                "telemetry": getattr(self.backend, "telemetry", None),
                "warnings": list(self.warnings)[-5:],
                "traj": None if self.traj is None else {
                    "name": self.traj["name"],
                    "progress": round(min(1.0, (time.monotonic() - self.traj["t0"]) / self.traj["dur"]), 2)},
            }
            measured = dict(self.measured)
        if measured:
            T = K.fk(measured)
            s["tool"] = {"xyz_mm": [round(v * 1000, 1) for v in T[:3, 3]], "pitch_deg": round(K.pitch_deg(T), 1)}
            if frames:
                s["frames"] = self._frames(measured)
        return s

    def _frames(self, angles):
        key = tuple(round(angles.get(j, 0.0), 1) for j in backends.JOINTS)
        if self._frames_cache[0] != key:
            F = link_frames(angles)
            self._frames_cache = (key, {n: [round(float(x), 5) for x in T.T.ravel()] for n, T in F.items()})
        return self._frames_cache[1]                      # column-major 4x4 (WebGL order)

    # ================================================================ internals (lock held)
    def _require_ready(self, motion=False):
        if self.backend is None or self.state not in ("ready", "parking"):
            raise RuntimeError(f"arm not ready (state: {self.state})")
        if motion and self.estop:
            raise RuntimeError("E-STOP is latched - press Reset first")
        if motion and not self.torque:
            raise RuntimeError("torque is off - turn it on to move the arm")
        if motion and self.state == "parking":
            raise RuntimeError("parking - wait until disconnected")

    def _clamp(self, j, v):
        lo, hi = self.limits[j]
        return min(hi, max(lo, v))

    def _pose(self, name):
        if name not in self.prof["poses"]:
            raise ValueError(f"unknown pose '{name}' (profile has {list(self.prof['poses'])})")
        return {k: float(v) for k, v in self.prof["poses"][name].items()}

    def _start_traj(self, end, name, duration=None, then=None):
        start = dict(self.goal)
        end = {k: self._clamp(k, v) for k, v in end.items() if k in self.limits}
        delta = max([abs(end[k] - start.get(k, end[k])) for k in end] + [0.0])
        if duration is None:
            duration = max(1.0, delta / (SPEEDS[self.speed]["joint"] * 1.5))
        self.traj = {"start": start, "end": end, "t0": time.monotonic(), "dur": float(duration),
                     "name": name, "then": then}
        self.jog["joints"], self.jog["cart"] = {}, {}
        self.owner = f"pose:{name}"

    # ================================================================ loop thread
    def _loop(self):
        dt = 1.0 / RATE_HZ
        last = time.monotonic()
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self._run_ops()
                if self.backend is not None and self.state in ("ready", "parking"):
                    self._tick(t0 - last)
            except Exception as e:                            # a bus error must not kill the loop
                self._fail(e)
            now = time.monotonic()
            if now > last:
                self.rate = 0.9 * self.rate + 0.1 / max(now - last, 1e-3)
            last = t0
            time.sleep(max(0.0, dt - (time.monotonic() - t0)))

    def _run_ops(self):
        while True:
            with self.lock:
                if not self._ops:
                    return
                op, arg = self._ops.popleft()
            if op == "connect":
                b = backends.make(arg, self.prof)
                try:
                    b.connect()
                    m = b.read()
                except Exception as e:
                    try:
                        b.close()
                    except Exception:
                        pass
                    with self.lock:
                        self.state, self.error = "error", f"connect failed: {e}"
                    continue
                with self.lock:
                    self.backend, self.measured = b, m
                    self.goal, self.target = dict(m), dict(m)
                    self.state, self.torque, self.estop, self.owner = "ready", True, False, "idle"
            elif op == "close":
                b = self.backend
                try:
                    if b is not None:
                        b.close()
                finally:
                    with self.lock:
                        self.backend, self.state, self.traj = None, "disconnected", None
                        self.measured, self.goal, self.target = {}, {}, {}
                        self.owner = "idle"
            elif op == "torque":
                self.backend.set_torque(arg)
                m = self.backend.read()
                with self.lock:
                    self.torque = arg
                    self.measured, self.goal, self.target = m, dict(m), dict(m)
                    self.owner = "idle" if arg else "hand-guiding"

    def _fail(self, e):
        with self.lock:
            self.error = f"{type(e).__name__}: {e}"
            self.warnings.append({"t": time.time(), "msg": self.error})
        traceback.print_exc()

    def _tick(self, dt):
        dt = min(max(dt, 1e-3), 0.1)
        m = self.backend.read()
        then = None
        with self.lock:
            self.measured = m
            if not self.torque:
                self.goal, self.target = dict(m), dict(m)      # hand-guiding: follow the arm
                goal = None
            elif self.estop:
                goal = dict(self.goal)
            else:
                then = self._update_target(dt, m)
                goal = self._rate_limit(dt)
                self._stall_guard(m, goal)
        if goal is not None:
            self.backend.write(goal)
        self.backend.poll_telemetry()
        if then == "close":
            with self.lock:
                self._ops.append(("close", None))

    def _update_target(self, dt, m):
        sp = SPEEDS[self.speed]
        if self.traj is not None:
            tr = self.traj
            u = min(1.0, (time.monotonic() - tr["t0"]) / tr["dur"])
            s = 0.5 - 0.5 * math.cos(math.pi * u)
            for k, v in tr["end"].items():
                a = tr["start"].get(k, v)
                self.target[k] = a + (v - a) * s
            if u >= 1.0:
                self.traj = None
                self.owner = "idle"
                return tr["then"]
            return None
        if time.monotonic() - self.jog["t"] > JOG_TIMEOUT_S:
            self.jog["joints"], self.jog["cart"] = {}, {}
            self._pitch_hold = None
        boost = BOOST if self.jog["boost"] else 1.0
        for j, v in self.jog["joints"].items():
            rate = sp["grip"] if j == "gripper" else sp["joint"]
            self.target[j] = self._clamp(j, self.goal[j] + v * rate * boost * dt)
        cart = self.jog["cart"]
        if cart:
            d = np.array([cart.get("x", 0.0), cart.get("y", 0.0), cart.get("z", 0.0)]) * sp["cart"] * boost * dt
            if self._pitch_hold is None:
                self._pitch_hold = K.pitch_deg(K.fk(self.goal))
            new = cart_step(self.goal, d, self._pitch_hold)
            p = K.fk(new)[:3, 3] if new else None
            if new is None:
                self._note("can't go further that way (edge of reach)")
            elif p[2] < TABLE_Z and d[2] <= 0:
                self._note("stopped at the table")
            elif math.hypot(p[0], p[1]) > self.max_radius and d[:2] @ p[:2] > 0:
                self._note("stopped at the edge of reach")
            elif any(not (self.limits[j][0] <= new[j] <= self.limits[j][1]) for j in K.ARM):
                j = next(j for j in K.ARM if not (self.limits[j][0] <= new[j] <= self.limits[j][1]))
                self._note(f"{j.replace('_', ' ')} is at its limit - try another direction or use the joint sliders")
            else:
                for j in K.ARM:
                    self.target[j] = new[j]
        return None

    def _note(self, msg):
        if not self.warnings or self.warnings[-1]["msg"] != msg:
            self.warnings.append({"t": time.time(), "msg": msg})

    def _rate_limit(self, dt):
        sp = SPEEDS[self.speed]
        fast = self.traj is not None or bool(self.jog["cart"])     # these are already smooth
        hand = self.owner == "hand"
        for j, tgt in self.target.items():
            rate = sp["grip"] if j == "gripper" else sp["joint"]
            if hand:
                rate *= HAND_SPEEDUP
            elif fast or self.jog["boost"]:
                rate *= 4.0 if fast else BOOST
            step = rate * dt
            self.goal[j] = self._clamp(j, self.goal[j] + max(-step, min(step, tgt - self.goal[j])))
        return dict(self.goal)

    def _stall_guard(self, m, goal):
        """Closing on an object: once the jaw stops moving, back the goal off to just past contact."""
        g, p = goal.get("gripper"), m.get("gripper")
        if g is None or p is None or self.backend.kind != "real":
            return
        now = time.monotonic()
        st = self._stall
        if p - g > 4.0 and (st["pos"] is None or abs(p - st["pos"]) > 0.5):
            st["since"], st["pos"] = now, p
        elif p - g <= 4.0:
            st["since"], st["pos"] = None, None
        elif st["since"] and now - st["since"] > STALL_S:
            eased = max(0.0, p - float(self.prof["gripper"].get("hold_squeeze", 2.0)))
            self.goal["gripper"] = self.target["gripper"] = eased
            goal["gripper"] = eased
            self.jog["joints"].pop("gripper", None)
            if self.owner == "hand":
                self._hand["grip_floor"] = eased
            st["since"], st["pos"] = None, None
            self._note(f"gripper is holding something at {p:.0f} - eased off to protect the motor")
