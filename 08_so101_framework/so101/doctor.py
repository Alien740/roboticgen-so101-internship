"""`so101 doctor`: check everything a run depends on, and (with --measure) measure the
robot's quirks so the profile matches the real hardware.

Exit code: 0 all good, 1 warnings, 2 errors. A JSON report is written to
runs/<profile>/doctor_report.json for AI agents.
"""
from __future__ import annotations

import importlib
import json
import platform
import sys
import time

OK, WARN, ERR = "ok", "warn", "error"


class Report:
    def __init__(self):
        self.items = []

    def add(self, area, status, msg, fix="", **data):
        self.items.append({"area": area, "status": status, "msg": msg, "fix": fix, **data})
        mark = {"ok": "  OK ", "warn": " WARN", "error": "ERROR"}[status]
        print(f"[{mark}] {area:12s} {msg}" + (f"\n{'':20s}-> {fix}" if fix else ""), flush=True)

    @property
    def code(self):
        s = {i["status"] for i in self.items}
        return 2 if ERR in s else 1 if WARN in s else 0


# ------------------------------------------------------------------ checks (no motion)
def check_environment(rep):
    v = sys.version_info
    rep.add("python", OK if v >= (3, 10) else ERR, f"{platform.system()} / Python {v.major}.{v.minor}.{v.micro}",
            "" if v >= (3, 10) else "install Python 3.10+ (3.12 tested)")
    for mod, why in [("lerobot", "robot driver"), ("scservo_sdk", "Feetech motor SDK (lerobot[feetech])"),
                     ("cv2", "OpenCV"), ("scipy", "IK solver"), ("yourdfpy", "URDF loader"), ("yaml", "profiles")]:
        try:
            m = importlib.import_module(mod)
            rep.add("package", OK, f"{mod} {getattr(m, '__version__', '')}".strip())
        except Exception as e:
            rep.add("package", ERR, f"{mod} missing ({why}): {type(e).__name__}", "run install.ps1 / install.sh")


def check_profile(rep, prof):
    rep.add("profile", OK, f"'{prof.name}' at {prof.folder}")
    motor_cal = prof.path(f"calibration/{prof['robot']['id']}.json")
    rep.add("calibration", OK if motor_cal.exists() else ERR, f"motor calibration {motor_cal.name}",
            "" if motor_cal.exists() else "run `lerobot-calibrate` for this arm and copy the json here")
    for key, label in (("table_file", "table (pixel->robot) calibration"), ("reference_image", "calibration reference view")):
        p = prof.path(prof["calibration"][key])
        rep.add("calibration", OK if p.exists() else WARN, f"{label}: {'present' if p.exists() else 'MISSING'}",
                "" if p.exists() else "run `so101 calibrate table` (needed for camera-guided tasks)")
    p = prof.path(prof["calibration"]["intrinsics_file"])
    rep.add("calibration", OK if p.exists() else WARN,
            f"camera intrinsics: {'present' if p.exists() else 'not calibrated (approximate pinhole in use)'}",
            "" if p.exists() else "optional: `so101 calibrate camera` with a printed checkerboard")


def check_motors(rep, prof):
    from .hardware.motors import Bus, FAULT_HELP, find_robot_port, list_ports
    r = prof["robot"]
    ports = list_ports()
    rep.add("serial", OK if ports else ERR, f"ports: {[p['device'] + ' (' + p['description'] + ')' for p in ports]}",
            "" if ports else "plug in the arm's USB cable; install the CH343/CH340 driver if no port appears")
    want = None if r.get("port", "auto") == "auto" else r["port"]
    port, tried = find_robot_port(r.get("baudrate", 1_000_000), preferred=want)
    if port is None:
        rep.add("motors", ERR, f"no port answers with 6 motors: {tried}",
                "check the arm's POWER supply (motors need it; USB alone is not enough), then retry")
        return None
    rep.add("motors", OK, f"all 6 motors answer on {port}", port=port)
    vnom = r.get("supply_voltage", 5.0)
    with Bus(port, r.get("baudrate", 1_000_000)) as b:
        for mid in range(1, 7):
            m = b.motor_report(mid)
            if not m["reply"]:
                rep.add("motor", ERR, f"{mid} {m['name']}: no reply", "check the daisy-chain cable to this motor"); continue
            st = OK
            notes = [f"{m['voltage']} V", f"{m['temp_c']} C"]
            fixes = []
            if m["voltage"] and abs(m["voltage"] - vnom) > 0.2 * vnom:
                st = WARN; fixes.append(f"voltage far from {vnom} V: check the power adapter")
            if m["temp_c"] and m["temp_c"] > 55:
                st = WARN; fixes.append("hot: let it cool")
            for f in m["faults"]:
                st = ERR; fixes.append(f"{f}: {FAULT_HELP.get(f, '')}")
            rep.add("motor", st, f"{mid} {m['name']:13s} " + ", ".join(notes) + (f"  FAULTS {m['faults']}" if m["faults"] else ""),
                    " | ".join(fixes), motor=m)
    return port


def check_camera(rep, prof):
    from .hardware.camera import camera_shift, find_camera
    c = prof["camera"]
    want = c.get("index", "auto")
    idx, found = find_camera(c["width"], c["height"], prefer=want if isinstance(want, int) else
                             prof.get("measured", {}).get("camera", {}).get("index"))
    desc = ", ".join(f"#{f['index']}{' black' if f['black'] else ''}{' static/virtual' if f['static'] else ''}" for f in found)
    if idx is None:
        rep.add("camera", ERR, f"no usable camera (found: {desc or 'none'})",
                "plug in the USB camera; close other apps using it (OBS, Teams, Zoom)")
        return None
    if isinstance(want, int) and want != idx:
        rep.add("camera", WARN, f"profile says index {want} but the live camera is #{idx} ({desc})",
                "Windows re-numbers cameras on replug: set camera.index to auto or the new number")
    else:
        rep.add("camera", OK, f"using camera #{idx} ({desc})", index=idx)
    # calibration freshness
    import cv2
    from .hardware.camera import Camera
    ref = prof.path(prof["calibration"]["reference_image"])
    if ref.exists():
        with Camera(idx, c["width"], c["height"]) as cam:
            f = cam.read()
        s = camera_shift(cv2.imread(str(ref)), f)
        lim = c.get("max_shift_px", 3.0)
        rep.add("camera", OK if s <= lim else WARN, f"shift since calibration {s:.1f} px (limit {lim})",
                "" if s <= lim else "camera was bumped: put it back, or re-run `so101 calibrate table`")
    tf = prof.path(prof["calibration"]["table_file"])
    if tf.exists():
        import numpy as np
        d = json.loads(tf.read_text())
        loo = d.get("loo_mm") or []
        if loo:
            med = float(np.median(loo))
            rep.add("calibration", OK if med < 5 else WARN,
                    f"table calibration: {len(d.get('points_px', []))} points, hold-one-out median {med:.1f} mm, max {max(loo):.1f} mm",
                    "" if med < 5 else "accuracy is poor: re-run `so101 calibrate table`")
    return idx


def check_kinematics(rep):
    from . import kinematics as K
    try:
        seed = {"shoulder_pan": 0, "shoulder_lift": 0, "elbow_flex": 0, "wrist_flex": 80}
        sol = K.ik((0.22, 0.0, 0.03), seed, roll=0.0, gripper=0)
        ok = sol is not None and sol[1] < 0.002
        rep.add("kinematics", OK if ok else ERR, "URDF loads, IK solves a test point" if ok else "IK self-test failed")
    except Exception as e:
        rep.add("kinematics", ERR, f"kinematics error: {e}", "reinstall; check so101/assets/so101_new_calib.urdf")


# ------------------------------------------------------------------ measurement (moves the arm)
def measure(rep, prof, session):
    """Measure quirks with the real arm. Asks before anything moves."""
    from .hardware.arm import Arm
    from .hardware.camera import Camera
    if not session.confirm("MEASURE moves the arm: wrist roll sweep, gripper open/close, a small pan move. "
                           "Clear the table around the arm."):
        rep.add("measure", WARN, "skipped by operator"); return
    with Arm(prof) as arm:
        prof.save_measured("robot", {"port": arm.port})
        arm.park()
        # --- wrist roll range: step out each way until the motor strains or lags
        lim = {}
        for sign in (+1, -1):
            last_ok = 0.0
            for tgt in range(10, 175, 10):
                t = sign * tgt
                arm.move_joints({**arm.pose("clear_view"), "wrist_roll.pos": float(t)}, duration=0.8)
                time.sleep(0.3)
                got, load = arm.angles()["wrist_roll"], abs(arm.load("wrist_roll"))
                if load > 900 or abs(got - t) > 8:
                    session.event("roll_limit", side=sign, commanded=t, measured=round(got, 1), load=load)
                    break
                last_ok = t
            lim[sign] = last_ok
        arm.park()
        lo, hi = lim[-1] + 5 if lim[-1] < -10 else lim[-1], lim[+1] - 5 if lim[+1] > 10 else lim[+1]
        prof.save_measured("wrist", {"roll_min_ok": float(lo), "roll_max_ok": float(hi)})
        w = prof["wrist"]
        st = OK if (w["roll_min_ok"] >= lo - 1 and w["roll_max_ok"] <= hi + 1) else WARN
        rep.add("measure", st, f"wrist roll free range {lo:.0f}..{hi:.0f} deg (profile {w['roll_min_ok']}..{w['roll_max_ok']})",
                "" if st == OK else f"edit wrist.roll_min_ok / roll_max_ok in robot.yaml to {lo:.0f} / {hi:.0f}")
        # --- gripper: where does an EMPTY gripper really close/open at the working torque?
        g = prof["gripper"]
        arm.set_grip_torque(g["torque"])
        opened = arm.open()
        t0 = time.time(); closed = arm.close(); t_close = time.time() - t0
        arm.open()
        prof.save_measured("gripper", {"empty_open": round(opened, 1), "empty_close": round(closed, 1),
                                       "close_time_s": round(t_close, 2), "torque": g["torque"]})
        st = OK if closed < g["empty_threshold"] and opened > 90 else WARN
        rep.add("measure", st, f"gripper empty: opens to {opened:.1f}, closes to {closed:.1f} at torque {g['torque']}",
                "" if st == OK else "gripper too weak/stiff at this torque: raise gripper.torque or set empty_threshold "
                                    f"to {closed + 1.5:.1f}")
        arm.close()
        # --- camera latency: time from a small pan command until the image changes
        try:
            with Camera.from_profile(prof) as cam:
                import numpy as np
                base = cam.read().astype(int)
                j = arm.joints()
                t0 = time.time()
                arm.robot.send_action({**j, "shoulder_pan.pos": j["shoulder_pan.pos"] + 8})
                lat = None
                for _ in range(60):
                    if np.abs(cam.read().astype(int) - base).mean() > 1.5:
                        lat = time.time() - t0; break
                arm.park(duration=1.5)
                prof.save_measured("camera", {"index": cam.index, "latency_s": None if lat is None else round(lat, 2)})
                rep.add("measure", OK if lat is not None and lat < 0.6 else WARN,
                        f"camera latency {'n/a' if lat is None else f'{lat:.2f} s'}",
                        "" if lat is not None and lat < 0.6 else "frames are stale: check camera USB bandwidth / close other apps")
        except Exception as e:
            rep.add("measure", WARN, f"camera latency not measured: {e}")
    rep.add("measure", OK, f"saved to {prof.folder / 'measured.yaml'}")


def run(prof, do_measure=False, session=None):
    print(f"so101 doctor - profile '{prof.name}'\n", flush=True)
    rep = Report()
    check_environment(rep)
    check_profile(rep, prof)
    check_kinematics(rep)
    port = check_motors(rep, prof)
    check_camera(rep, prof)
    if do_measure:
        if port is None or rep.code == 2:
            rep.add("measure", ERR, "not run: fix the errors above first")
        else:
            measure(rep, prof, session)
    out = prof.run_dir / "doctor_report.json"
    out.write_text(json.dumps({"profile": prof.name, "code": rep.code, "items": rep.items}, indent=2, default=str))
    print(f"\nresult: {['ALL GOOD', 'WARNINGS', 'ERRORS'][rep.code]}  (report: {out})", flush=True)
    return rep.code
