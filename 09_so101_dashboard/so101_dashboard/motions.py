"""Pre-programmed motions for the Motions tab: keyframes (seconds, joint degrees).

Played by the controller's playback (the same path as recordings): it eases from wherever the arm
is to the first frame, then interpolates between frames at the preset speed limits, with the
E-STOP, joint limits and smooth accel/brake applying as usual. Every frame keeps the gripper well
above the table (tests/test_dashboard.py checks it).
"""
from __future__ import annotations

UP = {"shoulder_pan": 0.0, "shoulder_lift": -60.0, "elbow_flex": -30.0, "wrist_flex": 0.0,
      "wrist_roll": 0.0, "gripper": 40.0}


def _p(**kw):
    return {**UP, **kw}


def _seq(*frames, step=0.8):
    """frames are poses (evenly spaced by `step` s) or (seconds, pose) pairs."""
    out, t = [], 0.0
    for f in frames:
        if isinstance(f, tuple):
            t, f = f
        out.append([round(t, 2), f])
        t += step
    return out


MOTIONS = {
    "hello": {"label": "Wave hello", "frames": _seq(
        UP, _p(shoulder_pan=20, wrist_flex=-30), _p(shoulder_pan=-20, wrist_flex=30),
        _p(shoulder_pan=20, wrist_flex=-30), _p(shoulder_pan=-20, wrist_flex=30), UP)},
    "yes": {"label": "Nod yes", "frames": _seq(
        UP, _p(wrist_flex=35), _p(wrist_flex=-25), _p(wrist_flex=35), _p(wrist_flex=-25), UP, step=0.6)},
    "no": {"label": "Shake no", "frames": _seq(
        UP, _p(shoulder_pan=25), _p(shoulder_pan=-25), _p(shoulder_pan=25), _p(shoulder_pan=-25), UP, step=0.6)},
    "dance": {"label": "Dance", "frames": _seq(
        UP,
        _p(shoulder_pan=30, shoulder_lift=-40, elbow_flex=-10, wrist_roll=45, gripper=90),
        _p(shoulder_pan=-30, shoulder_lift=-70, elbow_flex=-40, wrist_roll=-45, gripper=10),
        _p(shoulder_pan=30, shoulder_lift=-40, elbow_flex=-10, wrist_roll=45, gripper=90),
        _p(shoulder_pan=-30, shoulder_lift=-70, elbow_flex=-40, wrist_roll=-45, gripper=10),
        _p(wrist_flex=40), _p(wrist_flex=-40), UP, step=0.9)},
    "bow": {"label": "Take a bow", "frames": _seq(
        UP, (1.5, _p(shoulder_lift=-25, elbow_flex=40, wrist_flex=40)), (3.0, _p(shoulder_lift=-25, elbow_flex=40, wrist_flex=40)),
        (4.5, UP))},
    "chomp": {"label": "Chomp (gripper)", "frames": _seq(
        UP, _p(gripper=90), _p(gripper=5), _p(gripper=90), _p(gripper=5), _p(gripper=40), step=0.5)},
}
