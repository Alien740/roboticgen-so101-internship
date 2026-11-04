"""Hardware backends for the dashboard: the real arm (lerobot) or a simulated one.

Both expose the same small interface, called ONLY from the controller's loop thread (the
serial bus is not thread-safe):
    connect() / close(park_pose)   read() -> {joint: deg}   write({joint: deg})
    set_torque(on)                 poll_telemetry()  (one register per call, round-robin)
    telemetry: {motor: {voltage, temp_c, load, torque_on, faults}}

Lessons kept from the framework's Arm class:
- the per-step cap (max_relative) stays on the arm joints but is lifted for the gripper; with
  the cap a jaw slowed by friction stalls half-open.
- before torque is switched ON the goal is first set to the present position, otherwise the
  servo jumps to whatever stale goal it last had.
"""
from __future__ import annotations

import random
import time

from so101 import kinematics as K
from so101.hardware.motors import MOTORS, STATUS_BITS

JOINTS = list(K.JOINT_LIMITS)
_REGS = ("Present_Voltage", "Present_Temperature", "Present_Load", "Status", "Torque_Enable")


def _blank_telemetry():
    return {m: {"id": i, "voltage": None, "temp_c": None, "load": None, "torque_on": None, "faults": []}
            for i, m in MOTORS.items()}


class SimBackend:
    """Joints follow the goal with a short lag and a speed limit; telemetry is made up but plausible."""
    kind = "sim"
    port = "simulated"

    def __init__(self, prof):
        self.prof = prof
        self.pos = {j: 0.0 for j in JOINTS}
        self.goal = dict(self.pos)
        self.torque_on = True
        self.telemetry = _blank_telemetry()
        self._t = time.monotonic()

    def connect(self):
        home = self.prof["poses"]["home"]
        self.pos = {j: float(home.get(j, 0.0)) for j in JOINTS}
        self.goal = dict(self.pos)

    def close(self, park_pose=None):
        pass

    def read(self):
        now = time.monotonic()
        dt, self._t = min(now - self._t, 0.1), now
        if self.torque_on:
            for j in JOINTS:
                err = self.goal[j] - self.pos[j]
                step = err * min(1.0, dt / 0.08)                       # ~80 ms lag like the real servos
                lim = 250.0 * dt
                self.pos[j] += max(-lim, min(lim, step))
        return dict(self.pos)

    def write(self, goal):
        self.goal.update(goal)

    def set_torque(self, on):
        self.goal = dict(self.pos)
        self.torque_on = on

    def poll_telemetry(self):
        for m, t in self.telemetry.items():
            err = abs(self.goal[m] - self.pos[m])
            t.update(voltage=round(5.0 + random.uniform(-0.05, 0.05), 2), temp_c=31 + int(err > 3),
                     load=int(min(1000, err * 25)) if self.torque_on else 0, torque_on=self.torque_on, faults=[])


class RealBackend:
    kind = "real"

    def __init__(self, prof):
        self.prof = prof
        self.arm = None
        self.port = None
        self.telemetry = _blank_telemetry()
        self._poll = 0

    def connect(self):
        from so101.hardware.arm import Arm
        arm = Arm(self.prof)
        arm.__enter__()                       # retries, fault check, torque limits
        cap = float(self.prof["motion"]["max_relative"])
        arm.robot.config.max_relative_target = {j: (100.0 if j == "gripper" else cap) for j in JOINTS}
        self.arm, self.port = arm, arm.port

    def close(self, park_pose=None):
        if self.arm is None:
            return
        try:
            self.arm.robot.disconnect()       # disable_torque_on_disconnect=True
        finally:
            self.arm = None

    @property
    def bus(self):
        return self.arm.robot.bus

    def read(self):
        return {k: float(v) for k, v in self.bus.sync_read("Present_Position").items()}

    def write(self, goal):
        self.arm.robot.send_action({f"{j}.pos": float(v) for j, v in goal.items()})

    def set_torque(self, on):
        if on:
            self.bus.sync_write("Goal_Position", self.bus.sync_read("Present_Position"))
            self.bus.enable_torque()
        else:
            self.bus.disable_torque()

    def poll_telemetry(self):
        """Read one register of one motor (keeps each control tick short)."""
        motors = list(MOTORS.values())
        m = motors[self._poll % len(motors)]
        reg = _REGS[(self._poll // len(motors)) % len(_REGS)]
        self._poll += 1
        try:
            v = self.bus.read(reg, m, normalize=False)
        except Exception:
            self.telemetry[m]["faults"] = ["no reply"]
            return
        t = self.telemetry[m]
        if reg == "Present_Voltage":
            t["voltage"] = v / 10
        elif reg == "Present_Temperature":
            t["temp_c"] = int(v)
        elif reg == "Present_Load":
            t["load"] = (-1 if v & 0x400 else 1) * (v & 0x3FF)
        elif reg == "Status":
            t["faults"] = [name for bit, name in STATUS_BITS.items() if v & bit]
        else:
            t["torque_on"] = bool(v)


def make(kind, prof):
    return {"sim": SimBackend, "real": RealBackend}[kind](prof)

