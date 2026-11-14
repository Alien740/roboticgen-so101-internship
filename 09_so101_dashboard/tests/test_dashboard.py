"""Dashboard tests with the simulated arm (no robot, no camera)."""
import json
import math
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from so101 import kinematics as K
from so101 import profile as P
from so101_dashboard import mesh
from so101_dashboard.frames import link_frames
from so101_dashboard.controller import Controller, cart_step
from so101_dashboard.server import App, ThreadingHTTPServer, make_handler

WORK = {"shoulder_pan": 0.0, "shoulder_lift": 0.0, "elbow_flex": 0.0, "wrist_flex": 70.0, "wrist_roll": 0.0, "gripper": 0.0}


def wait(pred, timeout=5.0):
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < timeout, "timed out"
        time.sleep(0.02)


@pytest.fixture
def ctl():
    c = Controller(P.load("my_so101")).start()
    c.connect("sim")
    wait(lambda: c.state == "ready")
    yield c
    c.shutdown(park=False)


def test_link_frames_agree_with_fk():
    a = {"shoulder_pan": 10, "shoulder_lift": 20, "elbow_flex": -30, "wrist_flex": 60, "wrist_roll": 15, "gripper": 40}
    F = link_frames(a)
    assert np.allclose(F["gripper_frame_link"], K.fk(a))
    assert "moving_jaw_so101_v1_link" in F


def test_twin_mesh_is_shipped_and_matches_urdf():
    m = mesh.load()
    assert m is not None, "static/twin_mesh.json missing"
    links = set(link_frames(WORK))
    assert {p["link"] for p in m["parts"]} <= links
    assert 5_000 < m["triangles"] < 60_000


def test_cart_step_moves_straight_and_keeps_pitch():
    T = K.fk(WORK)
    new = cart_step(WORK, [0.0, 0.0, -0.01], K.pitch_deg(T))
    T2 = K.fk(new)
    assert np.allclose(T2[:3, 3] - T[:3, 3], [0, 0, -0.01], atol=5e-4)
    assert abs(K.pitch_deg(T2) - K.pitch_deg(T)) < 0.5


def test_goto_and_cartesian_jog(ctl):
    ctl.goto(WORK, duration=0.6)
    wait(lambda: ctl.traj is None)
    time.sleep(0.3)
    z0 = ctl.snapshot()["tool"]["xyz_mm"][2]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.5:
        ctl.jog_input(cart={"z": -1}, source="test")
        time.sleep(0.05)
    ctl.jog_input()
    time.sleep(0.3)
    dz = ctl.snapshot()["tool"]["xyz_mm"][2] - z0
    assert -35 < dz < -15                       # 0.05 m/s for 0.5 s ~ 25 mm


def test_jog_stops_by_itself_when_input_stops(ctl):
    ctl.jog_input(joints={"shoulder_pan": 1})
    time.sleep(0.8)
    a = ctl.snapshot()["measured"]["shoulder_pan"]
    time.sleep(0.4)
    assert abs(ctl.snapshot()["measured"]["shoulder_pan"] - a) < 0.2


def test_wrist_roll_limited_to_measured_free_range(ctl):
    ctl.set_joints({"wrist_roll": 120})
    assert ctl.snapshot()["target"]["wrist_roll"] == 75.0


def test_hand_input_reaches_target_and_clamps(ctl):
    for _ in range(60):                                   # ~3 s of hand frames at 20 Hz
        r = ctl.hand_input(pan=30, r=0.22, z=0.12, pitch=-40, roll=20, grip=60)
        time.sleep(0.05)
    assert r["reachable"]
    time.sleep(0.5)
    s = ctl.snapshot()
    x, y, z = (v / 1000 for v in s["tool"]["xyz_mm"])
    assert abs(math.degrees(math.atan2(y, x)) - 30) < 2
    assert abs(math.hypot(x, y) - 0.22) < 0.01 and abs(z - 0.12) < 0.01
    assert abs(s["measured"]["gripper"] - 60) < 2 and s["owner"] == "hand"
    out = ctl.hand_input(pan=500, r=9, z=-1, pitch=-200, roll=500)
    assert (out["pan"], out["r"], out["z"], out["pitch"]) == (90.0, 0.33, 0.02, -90.0)
    assert ctl.snapshot()["target"]["wrist_roll"] == 75.0


def test_mimic_copies_joints_ignores_noise_and_clamps(ctl):
    g = ctl.snapshot()["goal"]
    out = ctl.mimic_input({"shoulder_pan": g["shoulder_pan"] + 20, "elbow_flex": g["elbow_flex"] - 15, "gripper": 60})
    assert abs(out["shoulder_pan"] - (g["shoulder_pan"] + 20)) < 0.1 and ctl.snapshot()["owner"] == "mimic"
    before = ctl.snapshot()["target"]["shoulder_pan"]
    ctl.mimic_input({"shoulder_pan": before + 0.8})            # tracking noise: ignored
    assert ctl.snapshot()["target"]["shoulder_pan"] == before
    ctl.mimic_input({"wrist_roll": 150, "gripper": 100})        # clamped to the free range / short of the stop
    t = ctl.snapshot()["target"]
    assert t["wrist_roll"] == 75.0 and t["gripper"] == 95.0
    ctl.emergency_stop()
    with pytest.raises(RuntimeError):
        ctl.mimic_input({"shoulder_pan": 0})


def test_mimic_never_drives_the_tool_into_the_table(ctl):
    ctl.goto(WORK, duration=0.5); wait(lambda: ctl.traj is None)
    low = {"shoulder_lift": 90.0, "elbow_flex": 0.0, "wrist_flex": 60.0}    # tool would be 12 cm below z=0
    assert K.fk({**WORK, **low})[2, 3] < 0
    ctl.mimic_input(low)
    t = ctl.snapshot()["target"]
    assert K.fk(t)[2, 3] >= 0.005 - 1e-4                                    # stopped at the table


def test_hand_input_refused_during_estop(ctl):
    ctl.emergency_stop()
    with pytest.raises(RuntimeError):
        ctl.hand_input(pan=0, r=0.2, z=0.1, pitch=-40)


def test_motion_ramps_up_and_down_smoothly():
    c = Controller(P.load("my_so101"))                      # step the loop's maths directly, 50 Hz
    c.goal = dict(WORK); c.target = dict(WORK, shoulder_pan=30.0)
    dt, pos = 0.02, []
    for _ in range(150):
        c._rate_limit(dt); pos.append(c.goal["shoulder_pan"])
    v = np.diff([0.0] + pos) / dt
    acc = 35.0 * 4.0                                        # normal speed x ACCEL_PER_RATE
    assert v[0] <= acc * dt + 1e-6                          # starts gently (the old loop jumped to 35)
    assert np.abs(np.diff(v)).max() <= acc * dt * 1.25      # never a velocity step (old loop: 35)
    assert v.max() <= 35.0 + 1e-6 and abs(pos[-1] - 30.0) < 1e-6 and abs(v[-1]) < 1e-6


def test_lost_position_reads_do_not_raise(ctl):
    real, misses = ctl.backend.read, iter(range(3))

    def flaky():
        if next(misses, None) is not None:
            raise ConnectionError("There is no status packet!")
        return real()
    ctl.backend.read = flaky
    time.sleep(0.3)
    s = ctl.snapshot()
    assert s["state"] == "ready" and s["error"] == ""


def test_repeated_warnings_collapse():
    c = Controller(P.load("my_so101"))
    for _ in range(3):
        c.warn("no status packet")
    assert [w["msg"] for w in c.warnings] == ["no status packet (x3)"]


def test_estop_freezes_and_latches(ctl):
    ctl.set_joints({"shoulder_pan": 60})
    time.sleep(0.3)
    ctl.emergency_stop()
    a = ctl.snapshot()["measured"]["shoulder_pan"]
    time.sleep(0.4)
    assert abs(ctl.snapshot()["measured"]["shoulder_pan"] - a) < 0.5
    with pytest.raises(RuntimeError, match="E-STOP"):
        ctl.jog_input(joints={"elbow_flex": 1})
    ctl.reset_estop()
    ctl.jog_input(joints={"elbow_flex": 1})


def test_disconnect_parks_at_home_first(ctl):
    ctl.set_joints({"shoulder_pan": 30})
    time.sleep(0.5)
    ctl.disconnect()
    assert ctl.state == "parking"
    wait(lambda: ctl.state == "disconnected", timeout=10)


@pytest.fixture
def server():
    app = App(P.load("my_so101"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", app
    app.ctl.shutdown(park=False)
    httpd.shutdown()


def _req(url, body=None, header=True):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"X-SO101": "1"} if header else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_server_endpoints(server):
    base, app = server
    code, body = _req(base + "/")
    assert code == 200 and b"SO-101" in body
    code, body = _req(base + "/api/info")
    info = json.loads(body)
    assert code == 200 and info["limits"]["wrist_roll"] == [-100.0, 75.0] and "home" in info["poses"]
    assert _req(base + "/api/connect", {"mode": "sim"}, header=False)[0] == 403     # no cross-site driving
    assert _req(base + "/api/connect", {"mode": "sim"})[0] == 200
    wait(lambda: app.ctl.state == "ready")
    code, body = _req(base + "/api/state")
    s = json.loads(body)
    assert s["mode"] == "sim" and "gripper_frame_link" in s["frames"]
    code, body = _req(base + "/api/goto", {"pose": "nope"})
    assert code == 400 and b"unknown pose" in body
    assert _req(base + "/static/../server.py")[0] == 404
