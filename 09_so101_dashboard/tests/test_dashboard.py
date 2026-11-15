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
from so101_dashboard.controller import HAND_BOX, Controller, cart_step
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
    wait(lambda: ctl.traj is None, timeout=10)                # slack: a busy PC slows the sim loop
    time.sleep(0.3)
    z0 = ctl.snapshot()["tool"]["xyz_mm"][2]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.5:
        ctl.jog_input(cart={"z": -1}, source="test")
        time.sleep(0.05)
    ctl.jog_input()
    time.sleep(0.3)
    dz = ctl.snapshot()["tool"]["xyz_mm"][2] - z0
    assert -40 < dz < -15                       # 0.05 m/s for 0.5 s ~ 25 mm (+ timing slack under load)


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
    assert (out["pan"], out["r"], out["z"], out["pitch"]) == (90.0, 0.33, HAND_BOX["z"][0], -90.0)
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
    assert K.fk(t)[2, 3] >= ctl.table_z - 1e-4                               # stopped at the table


def test_table_floor_follows_a_low_rest_pose():
    c = Controller(P.load("my_so101"))
    home = {k: float(v) for k, v in P.load("my_so101")["poses"]["home"].items()}
    c.table_z = min(0.005, K.fk(home)[2, 3] - 0.003)          # what connecting at home sets
    lean = dict(home, shoulder_lift=home["shoulder_lift"] + 8)  # leaning out from rest lowers the tool a bit
    assert K.fk(lean)[2, 3] < 0.005
    out = c._above_table(home, lean)
    assert out["shoulder_lift"] > home["shoulder_lift"] + 1     # was blocked when the floor was z = 5 mm


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


def test_lost_packets_while_sending_do_not_raise(ctl):
    real, misses = ctl.backend.write, iter(range(3))

    def flaky(goal):
        if next(misses, None) is not None:
            raise ConnectionError("Failed to sync read 'Present_Position' ... There is no status packet!")
        return real(goal)
    ctl.backend.write = flaky
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


# ---------------------------------------------------------------- motions, record / replay, tasks
def test_every_motion_keeps_the_gripper_above_the_table_and_in_limits():
    from so101_dashboard.motions import MOTIONS
    lim = Controller(P.load("my_so101")).limits
    for name, m in MOTIONS.items():
        for t, pose in m["frames"]:
            assert all(lim[j][0] <= v <= lim[j][1] for j, v in pose.items()), (name, t)
            assert K.fk(pose)[2, 3] > 0.05, (name, t, K.fk(pose)[2, 3])


def test_motion_plays_to_the_end_and_any_command_interrupts(ctl):
    from so101_dashboard.motions import MOTIONS
    ctl.set_speed("fast")
    ctl.play(MOTIONS["no"]["frames"], "no", speed=2.0)
    wait(lambda: ctl.snapshot()["owner"].startswith("play:"), timeout=10)
    wait(lambda: ctl.snapshot()["owner"] == "idle", timeout=15)
    last = MOTIONS["no"]["frames"][-1][1]
    time.sleep(0.5)
    assert abs(ctl.snapshot()["measured"]["shoulder_pan"] - last["shoulder_pan"]) < 2
    ctl.play(MOTIONS["dance"]["frames"], "dance")
    ctl.set_joints({"gripper": 50})                         # interrupts
    assert ctl.playback is None and ctl.traj is None


def test_record_and_replay(ctl):
    ctl.record_start()
    ctl.set_joints({"shoulder_pan": ctl.goal["shoulder_pan"] + 25})
    time.sleep(1.5)
    frames = ctl.record_stop()
    assert len(frames) > 10 and frames[-1][0] > 1.0
    pan_end = frames[-1][1]["shoulder_pan"]
    ctl.set_joints({"shoulder_pan": pan_end - 25}); time.sleep(1.5)
    ctl.play(frames, "rec")
    wait(lambda: ctl.snapshot()["owner"] == "idle" and ctl.playback is None and ctl.traj is None, timeout=15)
    time.sleep(0.5)
    assert abs(ctl.snapshot()["measured"]["shoulder_pan"] - pan_end) < 2


def test_task_runner_drives_a_framework_task(ctl):
    from so101_dashboard.tasks import TaskRunner
    prof = P.load("my_so101")
    tr = TaskRunner(prof, ctl)
    if tr.running():                                         # the shared task slot is busy: maybe the
        pytest.skip("a framework task is running (real session?) - not touching it")   # user's real session
    with pytest.raises(ValueError):
        tr.start("rm -rf /")                                 # only known tasks
    tr.start("demo")                                         # no hardware: practises prompt/answer
    wait(lambda: tr.state()["state"] == "waiting", timeout=30)
    assert ctl.backend is None                               # the dashboard let go of the arm first
    with pytest.raises(RuntimeError):
        ctl.connect("sim")                                   # refused while the task runs
    for _ in range(6):
        st = tr.state()
        if st["state"] == "ended":
            break
        if st["state"] == "waiting":
            tr.answer("12" if "mm" in (st["prompt"] or "") else "go")
        time.sleep(1.5)
    wait(lambda: tr.state()["state"] == "ended", timeout=30)
    assert any("DEMO" in l or "demo" in l.lower() for l in tr.state()["output"])


# ---------------------------------------------------------------- AI tab (scripted stand-in for Claude)
class _FakeClaude:
    """Replays scripted assistant turns; records what the agent sent back."""
    def __init__(self, turns):
        from types import SimpleNamespace as NS
        self.NS, self.turns, self.sent = NS, list(turns), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.sent.append(kw)
        NS = self.NS
        blocks, stop = self.turns.pop(0)
        content = [NS(type="text", text=b) if isinstance(b, str) else
                   NS(type="tool_use", id=f"t{len(self.sent)}{i}", name=b[0], input=b[1]) for i, b in enumerate(blocks)]
        return NS(content=content, stop_reason=stop)


def test_ai_agent_runs_robot_tools_through_the_controller(ctl):
    from so101_dashboard.ai import AIAgent, TOOLS
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in TOOLS)
    ctl.set_speed("fast")
    ag = AIAgent(ctl)
    ag._sdk_problem = lambda: None
    ag.backend = lambda: "api"                                          # the SDK tool loop, scripted                                      # no real key needed: scripted
    ag._client = _FakeClaude([
        ([("get_state", {})], "tool_use"),
        ([("move_gripper_to", {"reach_cm": 22, "height_cm": 8, "base_deg": 20, "pointing": "down"}),
          ("set_gripper", {"percent": 10})], "tool_use"),
        (["Done: the gripper is over the spot and closed."], "end_turn"),
    ])
    ag.ask("move over there and close")
    wait(lambda: not ag.busy, timeout=40)
    roles = [m["role"] for m in ag.log]
    assert roles[0] == "user" and roles[-1] == "assistant", ag.log
    assert not any(m["role"] == "error" for m in ag.log), ag.log
    s = ctl.snapshot()
    x, y, z = (v / 1000 for v in s["tool"]["xyz_mm"])
    assert abs(math.hypot(x, y) - 0.22) < 0.015 and abs(z - 0.08) < 0.015
    assert abs(math.degrees(math.atan2(y, x)) - 20) < 3 and s["measured"]["gripper"] < 15
    sent = ag._client.sent[-1]
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert ag.messages[-2]["content"][0]["type"] == "tool_result"        # results went back to Claude
    assert ag.messages[-1]["role"] == "assistant"                       # conversation left valid


def test_ai_agent_stops_on_estop(ctl):
    from so101_dashboard.ai import AIAgent
    ag = AIAgent(ctl)
    ag._sdk_problem = lambda: None
    ag.backend = lambda: "api"                                          # the SDK tool loop, scripted
    ag._client = _FakeClaude([([("wait", {"seconds": 0.5})], "tool_use"), ([("go_to_pose", {"name": "ready"})], "tool_use"),
                              (["never reached"], "end_turn")])
    ctl.emergency_stop()
    ag.ask("do things")
    wait(lambda: not ag.busy, timeout=10)
    assert any(m["role"] == "error" for m in ag.log)
    assert not any("never reached" in m["text"] for m in ag.log)
    assert not ag.messages or ag.messages[-1]["role"] != "user"           # nothing left dangling


def test_robot_cli_maps_commands_to_agent_tools():
    from so101_dashboard.robot_cli import parse
    assert parse(["move", "22", "5", "-10"]) == ("move_gripper_to", {"reach_cm": 22.0, "height_cm": 5.0, "base_deg": -10.0, "pointing": "down"})
    assert parse(["gripper", "0"]) == ("set_gripper", {"percent": 0.0})
    assert parse(["motion", "hello"]) == ("play_motion", {"name": "hello"})
    for bad in (["rm", "-rf"], ["move", "1"], []):
        with pytest.raises(SystemExit):
            parse(bad)


def test_playback_before_the_first_frame_holds_the_first_pose():
    c = Controller(P.load("my_so101"))
    c.goal = dict(WORK); c.target = dict(WORK)
    c.playback = {"frames": [[0.05, {"shoulder_pan": 10.0}], [1.0, {"shoulder_pan": 20.0}]],
                  "t0": time.monotonic(), "speed": 1.0, "name": "rec"}
    assert c._playback_target() is False and c.target["shoulder_pan"] == 10.0


def test_record_refuses_a_recording_where_nothing_moved(ctl):
    from so101_dashboard.server import App
    app = App.__new__(App); app.ctl = ctl; app.prof = P.load("my_so101"); app.rec_dir = app.prof.run_dir / "recordings"
    ctl.record_start(); time.sleep(0.6)
    with pytest.raises(RuntimeError, match="nothing was recorded"):
        app.command("record", {"op": "stop", "name": "still"})


def test_play_switches_torque_back_on(ctl):
    from so101_dashboard.server import App
    app = App.__new__(App); app.ctl = ctl; app.prof = P.load("my_so101")
    ctl.set_torque(False); wait(lambda: not ctl.torque)
    app.command("motion", {"name": "yes", "speed": 2.0})
    assert ctl.torque and (ctl.traj is not None or ctl.playback is not None)


def test_picture_to_strokes_to_dry_run():
    import base64
    import cv2
    import numpy as np
    from so101_dashboard import drawing
    img = np.full((200, 300), 250, np.uint8)
    cv2.rectangle(img, (60, 50), (240, 150), 20, 5)
    png = base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()
    v = drawing.vectorize({"image": "data:image/png;base64," + png, "mode": "lines", "size_mm": 60})
    assert 1 <= v["stats"]["strokes"] <= 2 and len(v["uv"]) == len(v["strokes"])
    plan = drawing.plan(P.load().name, {"strokes": v["uv"], "size_mm": 60})
    assert plan["ok"], plan["error"]
    assert plan["keys"] and "gripper_frame_link" in plan["keys"][0]["f"] and len(plan["keys"][0]["f"]["gripper_frame_link"]) == 16
    assert any(t[1] for t in plan["tips"])                                      # some pen-down points
