"""Dashboard tests with the simulated arm (no robot, no camera)."""
import json
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


# ------------------------------------------------------------------ subset 2: motions, record / replay
from so101_dashboard import motions as M
from so101_dashboard.controller import joint_limits


@pytest.mark.parametrize("name", list(M.BUILTINS))
def test_builtin_motions_are_safe_at_1x(name):
    problems, stats = M.check(M.BUILTINS[name](), joint_limits(P.load("my_so101")), 1.0)
    assert problems == [], problems
    assert stats["min_link_z_mm"] > 100


def test_check_refuses_too_fast_playback():
    problems, _ = M.check(M.scare(), joint_limits(P.load("my_so101")), 2.0)
    assert any("too fast" in p for p in problems)


def test_recording_is_trimmed_and_resampled():
    still = {j: 0.0 for j in M.JOINTS}
    samples = [(i * 0.02, dict(still)) for i in range(50)]                         # 1 s still
    samples += [(1.0 + i * 0.02, {**still, "shoulder_pan": i * 0.5}) for i in range(60)]   # moves 30 deg
    samples += [(2.2 + i * 0.02, {**still, "shoulder_pan": 29.5}) for i in range(50)]      # 1 s still
    m = M.from_recording(samples, "t")
    assert M.duration(m) < 1.6                                                     # still parts gone
    assert abs(m["t"][1] - 1 / M.RECORD_HZ) < 1e-6
    assert abs(M.sample(m, M.duration(m))["shoulder_pan"] - 29.5) < 0.2


def test_library_save_rename_delete(tmp_path):
    lib = M.Library(tmp_path)
    m = M.from_recording([(0, {j: 0.0 for j in M.JOINTS}), (1, {**{j: 0.0 for j in M.JOINTS}, "elbow_flex": 20.0})], "My Move")
    lib.save(m)
    assert "My Move" in [x["name"] for x in lib.list()]
    with pytest.raises(ValueError):
        lib.save(m)                                     # no silent overwrite
    lib.rename("My Move", "other")
    assert lib.get("other")["name"] == "other"
    with pytest.raises(ValueError):
        lib.delete("hello")                             # built-ins are protected
    lib.delete("other")
    assert [x["name"] for x in lib.list()] == list(M.BUILTINS)


def test_play_motion_then_stop(ctl):
    ctl.play_motion(M.hello(), 1.0)
    assert ctl.traj["kind"] == "goto" and ctl.queue[0]["kind"] == "play"
    wait(lambda: ctl.traj is not None and ctl.traj["kind"] == "play", timeout=10)
    ctl.stop_motion()
    assert ctl.traj is None and ctl.queue == []


def test_jog_cancels_a_playing_motion(ctl):
    ctl.play_motion(M.dance(), 1.0)
    ctl.jog_input(joints={"shoulder_pan": 1})
    assert ctl.traj is None and ctl.queue == []


def test_record_and_replay_through_the_api(server, tmp_path):
    base, app = server
    app.library = M.Library(tmp_path)
    _req(base + "/api/connect", {"mode": "sim"})
    wait(lambda: app.ctl.state == "ready")
    assert _req(base + "/api/record_start", {})[0] == 200
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.6:
        app.ctl.jog_input(joints={"shoulder_pan": 1})
        time.sleep(0.05)
    time.sleep(0.4)
    code, body = _req(base + "/api/record_stop", {"name": "hello"})
    assert code == 400 and b"built-in" in body              # name refused, still recording
    code, body = _req(base + "/api/record_stop", {"name": "pan"})
    assert code == 200 and json.loads(body)["name"] == "pan"
    code, body = _req(base + "/api/motions")
    assert "pan" in [m["name"] for m in json.loads(body)["motions"]]
    code, body = _req(base + "/api/motion_preview?name=pan&speed=1")
    assert code == 200 and json.loads(body)["problems"] == []
    assert _req(base + "/api/motion_play", {"name": "pan", "speed": 1})[0] == 200
    wait(lambda: app.ctl.traj is None, timeout=10)


def test_hand_guided_recording_switches_torque_off_and_back(ctl):
    ctl.start_recording(hand_guide=True)
    wait(lambda: not ctl.torque)
    ctl.stop_recording()
    wait(lambda: ctl.torque)
