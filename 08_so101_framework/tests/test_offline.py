"""Offline tests: no robot, no camera. Run with `python -m pytest -q`."""
import math
import threading
import time

import numpy as np
import pytest

from so101 import kinematics as K
from so101 import profile as P
from so101.session import Session, Stopped

SEED = {"shoulder_pan": 0, "shoulder_lift": 0, "elbow_flex": 0, "wrist_flex": 80, "wrist_roll": 0, "gripper": 0}


def test_profile_loads():
    prof = P.load("my_so101")
    assert prof["robot"]["id"] == "my_so101"
    assert prof.path(f"calibration/{prof['robot']['id']}.json").exists(), "motor calibration missing from profile"
    for sec in ("motion", "gripper", "wrist", "objects", "detection", "drawing"):
        assert sec in prof


def test_fk_matches_taught_grasp_pose():
    # GRASP pose taught by hand on this arm (pick_place_poses.json); FK put the tool ~12 mm above the table
    grasp = {"shoulder_pan": 4.92, "shoulder_lift": 28.15, "elbow_flex": -11.56, "wrist_flex": 80.79, "wrist_roll": -0.13}
    p = K.fk(grasp)[:3, 3]
    assert np.allclose(p, [0.249, -0.018, 0.012], atol=0.003)


@pytest.mark.parametrize("x", [0.15, 0.20, 0.25, 0.30])
@pytest.mark.parametrize("y", [-0.12, 0.0, 0.12])
def test_top_down_ik_reaches_workspace(x, y):
    if (x, y) == (0.15, 0.0):
        pytest.skip("15 cm straight ahead has no top-down solution at travel height (known)")
    sol = K.ik((x, y, 0.012), SEED, roll=0.0, gripper=0)
    assert sol is not None and sol[1] < 0.002 and sol[2] < -70


def test_roll_respects_measured_limits():
    prof = P.load("my_so101")
    lo, hi = prof["wrist"]["roll_min_ok"], prof["wrist"]["roll_max_ok"]
    a = K.ik((0.22, 0.05, 0.012), SEED, roll=0.0, gripper=0)[0]
    for axis in range(0, 180, 15):
        r = K.roll_for_axis(a, axis, 180.0, (lo, hi))
        assert lo <= r <= hi


def test_pen_tip_traces_the_shape():
    """The pen leans up to ~15 deg (wrist_flex at its stop), so the TIP is what must follow
    the shape: a 60 mm circle must come out 30.0 mm radius, and the motion must be smooth."""
    prof = P.load("my_so101")
    d = np.array(prof["wrist"]["pen_dir_tool"], float); d /= np.linalg.norm(d)
    seed = {**SEED, "wrist_flex": -50, "wrist_roll": 3}
    c, L = np.array(prof["drawing"]["center"]) + (0.01, 0.0), 0.08
    pts = [(c[0] + 0.03 * math.cos(t), c[1] + 0.03 * math.sin(t), 0.001) for t in np.linspace(0, 2 * math.pi, 120)]
    traj = K.pen_path(K.pen_ik(pts[0], seed, d, length=L), pts, d, length=L)
    tips = np.array([K.pen_tip(K.fk(t), d, L) for t in traj])
    r = np.hypot(tips[:, 0] - c[0], tips[:, 1] - c[1])
    assert np.allclose(r, 0.03, atol=0.0005)
    steps = [max(abs(traj[i + 1][j] - traj[i][j]) for j in K.J5) for i in range(len(traj) - 1)]
    assert max(steps) < 3


def test_session_agent_mode(tmp_path):
    s = Session(tmp_path, agent=True)
    threading.Timer(0.3, lambda: (tmp_path / "cmd.txt").write_text("go")).start()
    assert s.ask("ready?") == "go"
    threading.Timer(0.3, lambda: (tmp_path / "cmd.txt").write_text("stop")).start()
    with pytest.raises(Stopped):
        s.ask("again?")
    s.close()
    log = (tmp_path / "session.log").read_text()
    assert "@@WAIT" in log and "@@EVENT" in log
