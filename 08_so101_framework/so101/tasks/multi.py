"""`so101 multi [--objects cube lego ...]`: clear several different objects into the basket.

Same engine as `so101 basket`; each object uses its own profile entry (colour, real size,
grasp height, grip torque, wrist alignment). Logged to runs/<profile>/multi_trials.csv.
"""
from __future__ import annotations

from .basket import drop_one
from .common import Scene, log_row


def main(a, prof, sess):
    from ..hardware.arm import Arm
    names = a.objects or prof["tasks"]["multi"]["objects"]
    unknown = [n for n in names if n not in prof["objects"]]
    if unknown:
        raise SystemExit(f"unknown object(s) {unknown}; defined in the profile: {list(prof['objects'])}")
    scene = Scene(prof, sess)
    try:
        with Arm(prof) as arm:
            for i in range(a.trials):
                if i:
                    sess.ask("put the objects back on the table, then 'go'", ["go", "stop"])
                for n in names:
                    log_row(prof, sess, drop_one(arm, scene, prof, sess, n, task="multi"))
    finally:
        scene.close()
