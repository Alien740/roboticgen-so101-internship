"""Raw Feetech STS3215 bus access for diagnostics (no lerobot, no torque, no motion).

Used by `so101 doctor` so problems can be diagnosed even when lerobot cannot connect.
Register addresses: STS/SMS control table (same as lerobot's feetech tables).
"""
from __future__ import annotations

MOTORS = {1: "shoulder_pan", 2: "shoulder_lift", 3: "elbow_flex", 4: "wrist_flex", 5: "wrist_roll", 6: "gripper"}
REG = {"torque_enable": (40, 1), "torque_limit": (48, 2), "present_position": (56, 2),
       "present_load": (60, 2), "present_voltage": (62, 1), "present_temperature": (63, 1), "status": (65, 1)}
STATUS_BITS = {0x01: "voltage", 0x02: "angle_sensor", 0x04: "overheat", 0x08: "current", 0x20: "overload"}
FAULT_HELP = {
    "overload": "motor protection tripped (sustained stall, e.g. gripper clamped too long). "
                "Unplug the arm's POWER for 10 s and plug back in.",
    "overheat": "motor too hot. Let it cool for 10 minutes; reduce gripper torque / hold time.",
    "voltage": "supply voltage out of range. Check the power adapter and barrel plug.",
    "current": "over-current. Check for a jammed joint or a cable caught in the mechanism.",
    "angle_sensor": "magnetic encoder error. Power-cycle; if it persists the servo may be faulty.",
}


def _sdk():
    import scservo_sdk as scs
    return scs


def list_ports():
    from serial.tools import list_ports as lp
    return [{"device": p.device, "description": p.description} for p in lp.comports()]


class Bus:
    def __init__(self, port, baudrate=1_000_000):
        scs = _sdk()
        self.scs = scs
        self.port = scs.PortHandler(port)
        self.ph = scs.PacketHandler(0)
        if not self.port.openPort():
            raise RuntimeError(f"cannot open {port} (in use by another program?)")
        self.port.setBaudRate(baudrate)

    def close(self):
        self.port.closePort()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def ping(self, mid) -> bool:
        _, res, _ = self.ph.ping(self.port, mid)
        return res == self.scs.COMM_SUCCESS

    def read(self, mid, reg):
        addr, n = REG[reg]
        fn = self.ph.read1ByteTxRx if n == 1 else self.ph.read2ByteTxRx
        val, res, _ = fn(self.port, mid, addr)
        return val if res == self.scs.COMM_SUCCESS else None

    def motor_report(self, mid) -> dict:
        if not self.ping(mid):
            return {"id": mid, "name": MOTORS[mid], "reply": False}
        st = self.read(mid, "status") or 0
        load = self.read(mid, "present_load") or 0
        v = self.read(mid, "present_voltage")
        return {"id": mid, "name": MOTORS[mid], "reply": True,
                "voltage": None if v is None else v / 10, "temp_c": self.read(mid, "present_temperature"),
                "torque_on": bool(self.read(mid, "torque_enable")), "position": self.read(mid, "present_position"),
                "load": (-1 if load & 0x400 else 1) * (load & 0x3FF),
                "faults": [name for bit, name in STATUS_BITS.items() if st & bit]}


def find_robot_port(baudrate=1_000_000, preferred=None):
    """Port where all six motors answer. Returns (port or None, [per-port summary])."""
    ports = [p["device"] for p in list_ports()]
    if preferred and preferred in ports:
        ports.remove(preferred); ports.insert(0, preferred)
    tried = []
    for dev in ports:
        try:
            with Bus(dev, baudrate) as b:
                ids = [m for m in MOTORS if b.ping(m)]
        except Exception as e:
            tried.append({"port": dev, "error": str(e)[:80]}); continue
        tried.append({"port": dev, "motors": ids})
        if len(ids) == 6:
            return dev, tried
    return None, tried
