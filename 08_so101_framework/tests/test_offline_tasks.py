"""Offline tests for vision, calibration maths, task geometry, report and CLI (no hardware)."""
import importlib
import math

import cv2
import numpy as np
import pytest

from so101 import profile as P

MODULES = ["so101.vision.detect", "so101.vision.mapping", "so101.vision.preview", "so101.calibration.table",
           "so101.calibration.camera", "so101.calibration.hsv", "so101.tasks.common", "so101.tasks.pick_place",
           "so101.tasks.basket", "so101.tasks.multi", "so101.tasks.stack", "so101.tasks.draw", "so101.report",
           "so101.doctor", "so101.cli"]


@pytest.mark.parametrize("mod", MODULES)
def test_imports(mod):
    importlib.import_module(mod)


def test_calibration_grid_matches_the_proven_21_points():
    from so101.calibration.table import grid_points
    cfg = P.load()["tasks"]["table_calibration"]
    assert len(grid_points(cfg, "main")) == 9 and len(grid_points(cfg, "offset")) == 12
    allp = grid_points(cfg, "all")
    assert len(allp) == 21 and allp[0] == (0.15, -0.10)


def test_fit_direct_recovers_a_tilted_camera():
    from so101.vision.mapping import fit_direct
    K = np.array([[540, 0, 330], [0, 540, 235], [0, 0, 1.]])
    rvec, tvec = np.array([2.6, 0.3, 0.2]), np.array([-150., 60., 550.])
    base = np.array([[x, y] for x in (0.15, 0.21, 0.27) for y in (-0.1, 0, 0.1)])
    px = cv2.projectPoints(np.c_[base * 1000, np.full(9, 17.0)], rvec, tvec, K, np.zeros(5))[0].reshape(-1, 2)
    H, res, loo = fit_direct(px, base)
    assert res.max() < 0.5 and max(loo) < 2.0


def test_orange_detection_rejects_disc_and_skin():
    from so101.vision.detect import detect
    prof = P.load()
    img = np.full((480, 640, 3), (150, 175, 190), np.uint8)                          # wood-ish
    cv2.fillPoly(img, [cv2.boxPoints(((400, 260), (22, 22), 25)).astype(int)], (20, 70, 235))   # orange cube
    cv2.circle(img, (150, 150), 25, (20, 70, 235), -1)                                # orange disc
    cv2.rectangle(img, (480, 380), (560, 440), (120, 150, 210), -1)                   # skin-tone patch
    d = detect(cv2.GaussianBlur(img, (5, 5), 0), prof, "orange")
    assert len(d) == 1 and abs(d[0].center_px[0] - 400) < 3


def test_half_across_uses_real_size_and_rotation():
    from so101.tasks.common import half_across
    size = (0.0316, 0.0156)                                                           # lego
    box_aligned = np.array([[0, 0], [0.0316, 0], [0.0316, 0.0156], [0, 0.0156]])      # long side along x
    assert math.isclose(half_across(box_aligned, size, np.array([0, 1.0])), 0.0078, abs_tol=1e-6)
    assert math.isclose(half_across(box_aligned, size, np.array([1.0, 0])), 0.0158, abs_tol=1e-6)


def test_draw_shapes_close_and_fit():
    from so101.tasks.draw import SHAPES, shape
    for s in SHAPES:
        strokes = shape(s, 60)
        pts = np.vstack([np.array(st) for st in strokes])
        assert np.abs(pts).max() <= 30.0 + 1e-6
    sq = shape("square", 60)[0]
    assert sq[0] == sq[-1]


def test_report_summary():
    from so101.report import summarise
    row = lambda **k: {"detected": "1", "grasped": "1", "success": "1", "attempts": "1", "error_mm": "10",
                       "cycle_time_s": "22", "failure": "", **k}
    s = summarise([row(), row(error_mm="20"), row(success="0", failure="grasp missed x3 (gripper 0.4)", grasped="0")])
    assert s["success"].startswith("2/3") and s["position error"].startswith("15.0")
    assert s["failures"][0][0].startswith("grasp missed")


@pytest.mark.parametrize("argv", [["doctor"], ["doctor", "--measure"], ["profile", "list"], ["calibrate", "table"],
                                  ["calibrate", "hsv", "--color", "orange"], ["detect", "orange", "yellow"],
                                  ["basket", "--trials", "3"], ["multi", "--objects", "cube", "lego"],
                                  ["stack", "--hover"], ["draw"], ["report"], ["--agent", "pick-place"]])
def test_cli_parses(argv):
    from so101.cli import build_parser
    a = build_parser().parse_args(argv)
    assert callable(a.fn)


def test_sketch_strokes_are_centred_and_scaled():
    from so101.tasks.draw import fit_strokes
    out = fit_strokes([[(10, 10), (30, 10), (30, 50)], [(0, 0)]], 40)      # 1-point strokes are dropped
    pts = [p for st in out for p in st]
    assert len(out) == 1
    assert abs(max(p[1] for p in pts) - min(p[1] for p in pts) - 40) < 1e-9   # larger side -> 40 mm
    assert abs(max(p[0] for p in pts) + min(p[0] for p in pts)) < 1e-9       # centred


def test_drawing_to_the_side_stays_upright_from_the_base():
    import math
    from so101.tasks.draw import to_base
    ahead = to_base([[(0, 0), (0, 10)]], (0.28, 0.0))[0]                    # +v = away from the robot
    assert abs(ahead[1][0] - ahead[0][0] - 0.010) < 1e-9 and abs(ahead[1][1] - ahead[0][1]) < 1e-9
    c = (0.285 * math.cos(math.radians(47)), 0.285 * math.sin(math.radians(47)))
    side = to_base([[(0, 0), (0, 10)]], c)[0]                               # away = radially outward there
    d = (side[1][0] - side[0][0], side[1][1] - side[0][1])
    assert abs(math.degrees(math.atan2(d[1], d[0])) - 47) < 1e-6
