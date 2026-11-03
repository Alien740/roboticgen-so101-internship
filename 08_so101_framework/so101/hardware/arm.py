"""SO-101 arm controller built on lerobot's SOFollower, driven entirely by the robot profile.

Lessons encoded here:
- Connect retries: the bus drops a packet now and then during configure(); a motor can
  also be briefly missing at handshake (RuntimeError) right after a gripper release.
- move_xyz interpolates in JOINT space (fine in free air); move_linear follows a straight
  Cartesian line (needed near objects: joint-space descent swung the jaws 7 mm sideways).
- Gripper moves lift the per-step cap (max_relative): with it, a jaw slowed by friction only
  saw an 8-unit error and stalled half-open. Force stays bounded by the gripper Torque_Limit.
- Never hold the gripper at goal 0 for long: that is a permanent full-force stall and trips
  the servo's overload protection (only a power cycle clears it). Use hold() instead.
- On exit the arm goes to `home` (folded, stable) before torque is released.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .. import kinematics as K
from .motors import FAULT_HELP


class Arm:
    def __init__(self, prof, port=None):
        from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig
        self.prof = prof
        m, r = prof["motion"], prof["robot"]
        port = port or r.get("port", "auto")
        if port == "auto":
            from .motors import find_robot_port
            port, tried = find_robot_port(r.get("baudrate", 1_000_000),
                                          preferred=prof.get("measured", {}).get("robot", {}).get("port"))
            if port is None:
                raise RuntimeError(f"no port with all 6 motors answering ({tried}) - is the arm powered? run `so101 doctor`")
        self.port = port
        cfg = SOFollowerRobotConfig(
            id=r["id"], port=port, use_degrees=True, disable_torque_on_disconnect=True,
            max_relative_target=m["max_relative"], calibration_dir=prof.path("calibration"),
            position_p_coefficient=m["p_gain"], position_i_coefficient=m["i_gain"],
            position_d_coefficient=m["d_gain"],
        )
        self.robot = SOFollower(cfg)
        self.fps = m["interp_fps"]

    # ------------------------------------------------------------ lifecycle
    def __enter__(self):
        for attempt in range(3):
            try:
                self.robot.connect(calibrate=False)
                break
            except (ConnectionError, RuntimeError) as e:
                print(f"  connect attempt {attempt + 1} failed: {str(e)[:120]}", flush=True)
                try:
                    self.robot.bus.disconnect(disable_torque=False)
                except Exception:
                    pass
                if attempt == 2:
                    raise
                time.sleep(1.0)
        faults = self.faults()
        if faults:
            self.robot.disconnect()
            raise RuntimeError("MOTOR FAULT " + "; ".join(f"{k}: {v} -> {FAULT_HELP.get(v.split('/')[0], '')}"
                                                         for k, v in faults.items()))
        for j in ("shoulder_lift", "elbow_flex", "shoulder_pan"):
            self._write("Max_Torque_Limit", j, 1000)
        self.set_grip_torque(self.prof["gripper"]["torque"])
        return self

    def __exit__(self, *exc):
        try:
            self.move_joints(self.pose("home"), duration=3.0)
        finally:
            self.robot.disconnect()

    def _write(self, reg, motor, val):
        try:
            self.robot.bus.write(reg, motor, val, normalize=False)
            return True
        except Exception as e:
            print(f"  warn: {reg} {motor}: {e}", flush=True)
            return False

    def faults(self) -> dict:
        """Status register per motor: 0x01 voltage, 0x04 overheat, 0x20 overload."""
        bad = {}
        for name in K.JOINT_LIMITS:
            try:
                st = self.robot.bus.read("Status", name, normalize=False)
            except Exception as e:
                bad[name] = f"no reply ({str(e)[:40]})"; continue
            names = [n for b, n in ((0x01, "voltage"), (0x04, "overheat"), (0x20, "overload")) if st & b]
            if names:
                bad[name] = "/".join(names)
        return bad

    # ------------------------------------------------------------ state
    def joints(self):
        obs = self.robot.get_observation()
        return {k: float(v) for k, v in obs.items() if k.endswith(".pos")}

    def angles(self):
        return {k[:-4]: v for k, v in self.joints().items()}

    def tool_pose(self):
        T = K.fk(self.angles())
        return T[:3, 3].copy(), K.pitch_deg(T)

    def load(self, motor):
        return self.robot.bus.read("Present_Load", motor, normalize=False)

    def pose(self, name, keep=()):
        """Profile pose as an action dict; joints in `keep` are left where they are."""
        p = self.prof["poses"][name]
        return {f"{k}.pos": float(v) for k, v in p.items() if k not in keep}

    def torque(self, on):
        (self.robot.bus.enable_torque if on else self.robot.bus.disable_torque)()

    # ------------------------------------------------------------ motion
    def move_joints(self, target, duration=None):
        duration = duration or self.prof["motion"]["move_duration_s"]
        start = self.joints()
        steps = max(1, int(duration * self.fps))
        for i in range(1, steps + 1):
            s = 0.5 - 0.5 * math.cos(math.pi * i / steps)
            self.robot.send_action({k: start[k] + (target[k] - start[k]) * s for k in target})
            time.sleep(1.0 / self.fps)
        time.sleep(0.25)

    def park(self, keep_roll=False, duration=None):
        """Upright, out of the camera's view (NOT home: home rests the tool on the table)."""
        self.move_joints(self.pose("clear_view", keep=("wrist_roll",) if keep_roll else ()), duration)

    def move_xyz(self, xyz, gripper=None, duration=None, jaw_axis_deg=None, period=180.0):
        """Joint-space move to a top-down pose. jaw_axis_deg: jaws close along this direction."""
        cur = self.angles()
        g = cur["gripper"] if gripper is None else gripper
        sol = K.ik(xyz, cur, roll=cur.get("wrist_roll", 0.0), gripper=g)
        if sol is None:
            raise RuntimeError(f"IK failed for {np.round(xyz, 3)}")
        angles = sol[0]
        if jaw_axis_deg is not None:
            w = self.prof["wrist"]
            roll = K.roll_for_axis(angles, jaw_axis_deg, period, (w["roll_min_ok"], w["roll_max_ok"]))
            sol = K.ik(xyz, angles, roll=roll, gripper=g)   # tool point is off the roll axis: re-solve
            if sol is None:
                raise RuntimeError(f"IK failed for {np.round(xyz, 3)} at roll {roll:.0f}")
            angles = sol[0]
        self.move_joints({f"{k}.pos": v for k, v in angles.items()}, duration)
        return sol[1], sol[2]

    def move_linear(self, xyz, duration=2.0, step=0.005):
        """Straight-line tool move; pitch blends to a near-vertical end pose."""
        cur = self.angles()
        T0 = K.fk(cur)
        start, pitch0 = T0[:3, 3], K.pitch_deg(T0)
        target = np.asarray(xyz, float)
        end = K.ik(target, cur, roll=cur["wrist_roll"], gripper=cur["gripper"])
        pitch1 = end[2] if end is not None else pitch0
        n = max(2, int(np.ceil(np.linalg.norm(target - start) / step)) + 1)
        traj, seed = [cur], cur
        for i in range(1, n):
            p = start + (target - start) * i / (n - 1)
            a, err = K.ik_local(p, seed, pitch0 + (pitch1 - pitch0) * i / (n - 1))
            if err > 0.003:
                sol = K.ik(p, seed, roll=cur["wrist_roll"], gripper=cur["gripper"])
                if sol is None:
                    raise RuntimeError(f"IK failed on straight line at {np.round(p, 3)}")
                a = sol[0]
            traj.append(a); seed = a
        jumps = [max(abs(traj[i + 1][j] - traj[i][j]) for j in K.ARM) for i in range(len(traj) - 1)]
        if max(jumps) > 15:
            raise RuntimeError(f"straight-line path has a {max(jumps):.0f} deg joint jump")
        steps = max(1, int(duration * self.fps))
        for k in range(1, steps + 1):
            u = (0.5 - 0.5 * math.cos(math.pi * k / steps)) * (len(traj) - 1)
            i = min(int(u), len(traj) - 2)
            f = u - i
            self.robot.send_action({f"{j}.pos": traj[i][j] + (traj[i + 1][j] - traj[i][j]) * f for j in K.J5})
            time.sleep(1.0 / self.fps)
        time.sleep(0.25)

    def stream(self, traj, rate_hz=30, check=None):
        """Send precomputed joint poses at a fixed rate (soft start/stop). check(): raise to abort."""
        dt, n = 1.0 / rate_hz, len(traj)
        for i, A in enumerate(traj):
            if check:
                check()
            self.robot.send_action({f"{k}.pos": A[k] for k in K.J5})
            time.sleep(dt / max(min(1.0, (i + 1) / 8, (n - i) / 8), 0.4))
        time.sleep(0.2)

    # ------------------------------------------------------------ gripper
    def set_grip_torque(self, torque):
        ok = self._write("Max_Torque_Limit", "gripper", int(torque))
        return self._write("Torque_Limit", "gripper", int(torque)) and ok

    def gripper(self, value, duration=None):
        """Move only the gripper, with the per-step cap lifted (see module docstring)."""
        duration = duration or 1.0
        start = self.joints()
        cap = self.robot.config.max_relative_target
        self.robot.config.max_relative_target = None
        try:
            steps = max(1, int(duration * self.fps))
            for i in range(1, steps + 1):
                a = dict(start)
                a["gripper.pos"] = start["gripper.pos"] + (value - start["gripper.pos"]) * i / steps
                self.robot.send_action(a)
                time.sleep(1.0 / self.fps)
            time.sleep(0.6)
        finally:
            self.robot.config.max_relative_target = cap
        return self.joints()["gripper.pos"]

    def open(self):
        return self.gripper(self.prof["gripper"]["open"])

    def close(self, torque=None):
        """Close fully (for a grasp). Returns the stall position (= object width proxy)."""
        if torque:
            self.set_grip_torque(torque)
        return self.gripper(self.prof["gripper"]["close"])

    def hold(self, idle_torque=None):
        """For LONG holds (pen): back the goal off to just past contact, never 0."""
        g = self.prof["gripper"]
        p = self.joints()["gripper.pos"]
        self.gripper(max(p - g["hold_squeeze"], p * 0.5), duration=0.3)
        self.set_grip_torque(idle_torque or g["idle_torque"])
        return p

    def holding(self, pos=None) -> str:
        """'held' | 'empty' | 'jammed' from the gripper stall position."""
        g = self.prof["gripper"]
        pos = self.joints()["gripper.pos"] if pos is None else pos
        if pos < g["empty_threshold"]:
            return "empty"
        if pos > g["jam_threshold"]:
            return "jammed"
        return "held"
