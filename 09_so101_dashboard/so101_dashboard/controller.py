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
# the real table can sit below the base's z=0 (resting at home the tool reads z = -9..-24 mm): the
# floor is the lower of TABLE_Z and the tool height when the arm connected (it rests on the table)
TABLE_MARGIN = 0.003
STALL_S = 1.0
GRIP_OPEN_MAX = 95.0             # stop the jaw short of its open end stop (see joint_limits)
ACCEL_PER_RATE = 4.0             # joints reach full preset speed in 1/4 s and brake to a stop (no jerks)
HAND_HOLD_M = 0.004              # hand moved less than this (and tilted/turned less than HAND_HOLD_DEG)
HAND_HOLD_DEG = 4.0              # since the last accepted target = camera noise: the arm holds still
MIMIC_HOLD_DEG = 2.0             # copied arm joints: smaller changes than this are tracking noise
MIMIC_SPEEDUP = 2.0              # copied joints follow a bit slower than the hand cursor: smoother
READ_MISS_MAX = 10               # consecutive lost position reads (0.2 s) before it counts as an error
HAND_SPEEDUP = 3.0               # hand following uses the preset joint speed x this (it is pre-smoothed)
HAND_TIMEOUT_S = 0.5             # no hand frame for this long -> the arm holds where it is
HAND_BOX = {"r": (0.12, 0.33), "z": (0.008, 0.30), "pitch": (-90.0, 30.0), "pan": (-90.0, 90.0)}   # z: the profile grasps a cube at 12 mm
HAND_MAX_ERR = 0.015             # IK further than this from the hand target -> keep the last pose


def joint_limits(prof):
    lim = dict(K.JOINT_LIMITS)
    w = prof.get("wrist", {})
    lim["wrist_roll"] = (float(w.get("roll_min_ok", -180.0)), float(w.get("roll_max_ok", 180.0)))
    # 100 = the end stop recorded by calibration: holding it there pushes into the stop, trips the
    # gripper's (deliberately sensitive) overload protection and the jaw stops responding
    lim["gripper"] = (lim["gripper"][0], GRIP_OPEN_MAX)
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
        self.playback = None                 # {frames: [(t, {joint: deg})], t0, name, speed} motions / replays
        self.recording = None                # {t0, frames} while recording
        self.jog = {"joints": {}, "cart": {}, "boost": False, "t": 0.0, "source": ""}
        self._pitch_hold = None
        self._stall = {"since": None, "pos": None}
        self._hand = {"t": 0.0, "seed": None, "grip_floor": None}
        self._vel = {}                       # joint velocity of the goal (deg/s), for smooth accel/brake
        self.table_z = TABLE_Z               # floor for the tool point, see TABLE_MARGIN
        self._misses = 0
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
            self._note(msg)

    # ================================================================ commands (any thread)
    busy = staticmethod(lambda: None)        # set by the server: why the arm can't be connected now
    task_pose = None                          # set by the server: angles a running framework task commands

    def connect(self, kind):
        if kind not in ("sim", "real"):
            raise ValueError("mode must be 'sim' or 'real'")
        if self.busy():
            raise RuntimeError(f"{self.busy()} - stop it first")
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
            self.traj = self.playback = None
            self.jog["joints"], self.jog["cart"] = {}, {}
            self.owner = "E-STOP"
            if self.state == "parking":
                self.state = "ready"
            self._vel = {}
            if self.measured:
                self.goal = dict(self.measured)
                self.target = dict(self.measured)

    def reset_estop(self):
        with self.lock:
            self.estop = False
            self.owner = "idle"
            self._vel = {}
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
                self.traj = self.playback = None
                self.owner = f"manual:{source}"
            self.jog.update(joints=joints, cart=cart, boost=bool(boost), t=time.monotonic(), source=source)

    def set_joints(self, values, source="slider"):
        """Absolute targets (deg / gripper 0-100); the arm moves there at the preset speed."""
        with self.lock:
            self._require_ready(motion=True)
            self.traj = self.playback = None
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
        xyz = np.array([r * math.cos(math.radians(pan)), r * math.sin(math.radians(pan)), z])
        with self.lock:
            self._require_ready(motion=True)
            if time.monotonic() - self._hand["t"] > HAND_TIMEOUT_S or self._hand["seed"] is None:
                self._hand["seed"] = dict(self.goal)         # (re)engaging: start from where the arm is
                self._hand["held"] = None
            seed, held = dict(self._hand["seed"]), self._hand.get("held")
        # hold zone: IK amplifies tremor/camera noise (1 deg of pitch -> 1.5 deg at the shoulder),
        # so small wobbles keep the last accepted target and only real moves get through
        still = held is not None and np.linalg.norm(xyz - held[0]) < HAND_HOLD_M and abs(pitch - held[1]) < HAND_HOLD_DEG
        if still:
            xyz, pitch = held
        sol, err = (seed, 0.0) if still else K.ik_local(xyz, seed, pitch)
        edge = False
        if err >= HAND_MAX_ERR:
            # that tool angle can't be had there: keep the position, let the angle give, and if the
            # point itself is out of reach go to the closest pose (the arm stops at the edge, not dead)
            sol, err = K.ik_local(xyz, seed, pitch, w_pitch=0.3)
            edge = err >= HAND_MAX_ERR
        ok = all(self.limits[j][0] <= sol[j] <= self.limits[j][1] for j in K.ARM)
        with self.lock:
            self._require_ready(motion=True)
            self.traj = self.playback = None
            self.jog["joints"], self.jog["cart"] = {}, {}
            self._hand["t"] = time.monotonic()
            if ok and not still:
                self._hand["seed"], self._hand["held"] = sol, (xyz, pitch)
                for j in K.ARM:
                    self.target[j] = self._clamp(j, sol[j])
            elif not ok:
                self._note("that pose needs a joint past its limit - holding the last pose")
            if edge:
                self._note("hand is beyond the arm's reach - the arm stays at the edge")
            if roll is not None and abs(float(roll) - self.target.get("wrist_roll", 0.0)) >= HAND_HOLD_DEG:
                self.target["wrist_roll"] = self._clamp("wrist_roll", float(roll))
            if grip is not None:
                self._grip_follow(grip)
            self.owner = "hand"
        return {"pan": pan, "r": r, "z": z, "pitch": pitch, "reachable": bool(ok and not edge)}

    def mimic_input(self, joints):
        """Copy a tracked human arm: absolute joint targets (deg, gripper 0-100), sent at ~10-30 Hz
        by the browser's body tracking. Same path, speed and limits as hand following; joints that
        moved less than MIMIC_HOLD_DEG keep their target (tracking noise)."""
        with self.lock:
            self._require_ready(motion=True)
            self.traj = self.playback = None
            self.jog["joints"], self.jog["cart"] = {}, {}
            self._hand["t"] = time.monotonic()
            cand = dict(self.target)
            for k, v in joints.items():
                if k == "gripper":
                    self._grip_follow(v)
                elif k in self.limits:
                    v = self._clamp(k, float(v))
                    if abs(v - cand.get(k, v)) >= MIMIC_HOLD_DEG or k not in cand:
                        cand[k] = v
            cand["gripper"] = self.target.get("gripper", 0.0)
            self.target = self._above_table(self.target, cand)
            self.owner = "mimic"
        return {k: round(self.target[k], 1) for k in joints if k in self.target}

    def _above_table(self, cur, cand):
        """cand, or the furthest point toward it (joint-space line from cur) that keeps the tool
        above the table - the arm slides to a stop at the table instead of pushing into it. A cur
        that is already low (folded home rests the tool at z<0) may still move up."""
        z = lambda a: K.fk(a)[2, 3]
        zc = z(cand)
        floor = self.table_z
        if zc >= floor or zc >= z(cur):
            return cand
        if z(cur) < floor:
            self._note("stopped at the table")
            return dict(cur)
        lo, hi = 0.0, 1.0
        for _ in range(8):
            mid = (lo + hi) / 2
            if z({k: cur[k] + (cand[k] - cur[k]) * mid for k in cand}) >= floor:
                lo = mid
            else:
                hi = mid
        self._note("stopped at the table")
        return {k: cur[k] + (cand[k] - cur[k]) * lo for k in cand}

    def _grip_follow(self, grip):
        """Gripper target from a tracked hand (lock held). After the stall guard eased a grip the
        hand can't re-squeeze until it opens again (an endless stall trips the overload)."""
        g = self._clamp("gripper", float(grip))
        floor = self._hand["grip_floor"]
        if floor is not None:
            if g > floor + 10.0:
                self._hand["grip_floor"] = None
            else:
                g = max(g, floor)
        self.target["gripper"] = g

    # ---------------------------------------------------------------- motions / record & replay
    def play(self, frames, name="motion", speed=1.0):
        """Play timed keyframes [[t_s, {joint: deg}], ...]: ease to the first frame, then follow the
        frames (linear between them; the rate limiter smooths). Any other command interrupts."""
        frames = sorted(([float(t), {k: float(v) for k, v in f.items() if k in self.limits}] for t, f in frames),
                        key=lambda x: x[0])
        if not frames:
            raise ValueError("nothing to play")
        speed = max(0.25, min(2.0, float(speed)))
        with self.lock:
            self._require_ready(motion=True)
            self.playback = None
            first = frames[0][1]
            delta = max([abs(first[k] - self.goal.get(k, first[k])) for k in first] + [0.0])
            self._start_traj(first, f"to start of {name}", duration=max(1.0, delta / 40.0), then="play")
            self._pending_play = {"frames": frames, "name": name, "speed": speed}

    def record_start(self):
        with self.lock:
            if self.backend is None:
                raise RuntimeError("connect an arm first")
            self.recording = {"t0": time.monotonic(), "frames": []}

    def record_stop(self):
        with self.lock:
            rec, self.recording = self.recording, None
        if not rec or len(rec["frames"]) < 2:
            raise RuntimeError("nothing recorded (record for at least a second)")
        return rec["frames"]

    def _playback_target(self):
        """Lock held. Set targets from the playback frames; returns True when it finished."""
        pb = self.playback
        t = (time.monotonic() - pb["t0"]) * pb["speed"]
        fr = pb["frames"]
        if t >= fr[-1][0]:
            self.target.update({k: self._clamp(k, v) for k, v in fr[-1][1].items()})
            return True
        if t <= fr[0][0]:                                  # recordings start a few ms after 0
            self.target.update({k: self._clamp(k, v) for k, v in fr[0][1].items()})
            return False
        i = max(i for i, f in enumerate(fr) if f[0] <= t)
        (t0, a), (t1, b) = fr[i], fr[i + 1]
        u = (t - t0) / max(t1 - t0, 1e-6)
        for k, v in b.items():
            self.target[k] = self._clamp(k, a.get(k, v) + (v - a.get(k, v)) * u)
        return False

    def goto(self, pose, duration=None):
        """Smooth move to a profile pose name or a {joint: deg} dict (joints left out stay)."""
        with self.lock:
            self._require_ready(motion=True)
            self.playback = None
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
                "recording": None if self.recording is None else round(time.monotonic() - self.recording["t0"], 1),
                "playing": None if self.playback is None else {
                    "name": self.playback["name"],
                    "progress": round(min(1.0, (time.monotonic() - self.playback["t0"]) * self.playback["speed"]
                                          / max(self.playback["frames"][-1][0], 1e-6)), 2)},
                "traj": None if self.traj is None else {
                    "name": self.traj["name"],
                    "progress": round(min(1.0, (time.monotonic() - self.traj["t0"]) / self.traj["dur"]), 2)},
            }
            measured = dict(self.measured)
        if not measured and self.backend is None and self.task_pose:
            measured = self.task_pose() or {}                 # a framework task owns the arm: show its pose
            s["task_pose"] = bool(measured)
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
                    self.goal, self.target, self._vel = dict(m), dict(m), {}
                    self.table_z = min(TABLE_Z, K.fk(m)[2, 3] - TABLE_MARGIN)
                    self.state, self.torque, self.estop, self.owner = "ready", True, False, "idle"
            elif op == "close":
                b = self.backend
                try:
                    if b is not None:
                        b.close()
                finally:
                    with self.lock:
                        self.backend, self.state, self.traj = None, "disconnected", None
                        self.playback = self.recording = None
                        self.measured, self.goal, self.target = {}, {}, {}
                        self.owner = "idle"
            elif op == "torque":
                self.backend.set_torque(arg)
                m = self.backend.read()
                with self.lock:
                    self.torque = arg
                    self.measured, self.goal, self.target, self._vel = m, dict(m), dict(m), {}
                    self.owner = "idle" if arg else "hand-guiding"

    def _fail(self, e):
        with self.lock:
            self.error = f"{type(e).__name__}: {e}"
            self._note(self.error)
        traceback.print_exc()

    def _tick(self, dt):
        dt = min(max(dt, 1e-3), 0.1)
        try:
            m = self.backend.read()
            self._misses = 0
        except ConnectionError:
            # a lost status packet (power dip / bus noise): hold the goal for this tick; only a
            # sustained loss is an error worth showing
            self._misses += 1
            if self._misses < READ_MISS_MAX:
                return
            raise
        then = None
        with self.lock:
            self.measured = m
            rec = self.recording
            if rec is not None and (not rec["frames"] or time.monotonic() - rec["t0"] - rec["frames"][-1][0] >= 0.05):
                rec["frames"].append([round(time.monotonic() - rec["t0"], 3), {k: round(v, 2) for k, v in m.items()}])
            if not self.torque:
                self.goal, self.target = dict(m), dict(m)      # hand-guiding: follow the arm
                self._vel = {}
                goal = None
            elif self.estop:
                goal = dict(self.goal)
            else:
                then = self._update_target(dt, m)
                goal = self._rate_limit(dt)
                self._stall_guard(m, goal)
        if goal is not None:
            try:
                self.backend.write(goal)   # lerobot reads the positions again inside (max_relative_target)
            except ConnectionError:
                self._misses += 1          # same tolerance as the read above: next tick sends the goal again
                if self._misses >= READ_MISS_MAX:
                    raise
                return
        self.backend.poll_telemetry()
        if then == "close":
            with self.lock:
                self._ops.append(("close", None))
        elif then == "play":
            with self.lock:
                pb = getattr(self, "_pending_play", None)
                if pb is not None:
                    self.playback = {**pb, "t0": time.monotonic()}
                    self.owner = f"play:{pb['name']}"
                    self._pending_play = None

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
        if self.playback is not None:
            if self._playback_target():
                self.playback = None
                self.owner = "idle"
            return None
        if time.monotonic() - self.jog["t"] > JOG_TIMEOUT_S:
            self.jog["joints"], self.jog["cart"] = {}, {}
            self._pitch_hold = None
        boost = BOOST if self.jog["boost"] else 1.0
        for j, v in self.jog["joints"].items():
            rate = sp["grip"] if j == "gripper" else sp["joint"]
            base = self.target[j] if abs(self.target[j] - self.goal[j]) < 10.0 else self.goal[j]
            self.target[j] = self._clamp(j, base + v * rate * boost * dt)
        cart = self.jog["cart"]
        if cart:
            d = np.array([cart.get("x", 0.0), cart.get("y", 0.0), cart.get("z", 0.0)]) * sp["cart"] * boost * dt
            if self._pitch_hold is None:
                self._pitch_hold = K.pitch_deg(K.fk(self.goal))
            near = all(abs(self.target[j] - self.goal[j]) < 10.0 for j in K.ARM)
            new = cart_step(self.target if near else self.goal, d, self._pitch_hold)
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
        """Add a warning; a repeat of the last one only bumps its count and time."""
        last = self.warnings[-1] if self.warnings else None
        if last and last["base"] == msg:
            last["n"] += 1
            last["t"] = time.time()
            last["msg"] = f"{msg} (x{last['n']})"
        else:
            self.warnings.append({"t": time.time(), "msg": msg, "base": msg, "n": 1})

    def _rate_limit(self, dt):
        sp = SPEEDS[self.speed]
        fast = self.traj is not None or self.playback is not None or bool(self.jog["cart"])   # already smooth
        follow = {"hand": HAND_SPEEDUP, "mimic": MIMIC_SPEEDUP}.get(self.owner)
        for j, tgt in self.target.items():
            rate = sp["grip"] if j == "gripper" else sp["joint"]
            if follow:
                rate *= follow
            elif fast or self.jog["boost"]:
                rate *= 4.0 if fast else BOOST
            # velocity with limited acceleration, braking so it stops at the target (no jerks,
            # no bang-bang chatter around a noisy target)
            acc = rate * ACCEL_PER_RATE
            err = tgt - self.goal[j]
            v = self._vel.get(j, 0.0)
            # fastest speed that can still brake to 0 at the target in whole dt steps
            brake = acc * (math.sqrt(dt * dt / 4.0 + 2.0 * abs(err) / acc) - dt / 2.0)
            v_des = math.copysign(min(rate, brake), err)
            v += max(-acc * dt, min(acc * dt, v_des - v))
            step = v * dt
            if abs(step) >= abs(err) or step * err < 0:
                step, v = err, (err / dt if abs(err) / dt <= rate else math.copysign(rate, err))
            self._vel[j] = v
            self.goal[j] = self._clamp(j, self.goal[j] + step)
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
            self._vel["gripper"] = 0.0
            goal["gripper"] = eased
            self.jog["joints"].pop("gripper", None)
            if self.owner in ("hand", "mimic"):
                self._hand["grip_floor"] = eased
            st["since"], st["pos"] = None, None
            self._note(f"gripper is holding something at {p:.0f} - eased off to protect the motor")
