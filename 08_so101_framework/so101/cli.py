"""so101 - one entry point for every procedure (people and AI agents use the same commands).

    so101 doctor [--measure]           check setup; --measure records robot quirks (moves the arm)
    so101 profile show|list            print the active profile
    so101 calibrate camera|table|hsv   calibrations
    so101 detect [color]               live detection preview
    so101 pick-place | basket | multi | stack | draw ...   tasks
    so101 report                       metrics from logged trials

Global options: --profile NAME (or $SO101_PROFILE), --agent (or $SO101_AGENT=1).
In agent mode answer prompts by writing to runs/<profile>/cmd.txt ('stop' aborts motion).
"""
from __future__ import annotations

import argparse
import sys

from . import profile as P
from .session import Session, Stopped, stdout_utf8


def _cmd_doctor(a, prof):
    from . import doctor
    sess = Session(prof.run_dir, a.agent) if a.measure else None
    try:
        return doctor.run(prof, a.measure, sess)
    finally:
        if sess:
            sess.close()


def _cmd_profile(a, prof):
    import yaml
    if a.action == "list":
        print("\n".join(P.list_profiles()))
    else:
        print(f"# profile '{prof.name}' ({prof.folder})")
        print(yaml.safe_dump(dict(prof), sort_keys=False))
    return 0


def _task(module, func="main"):
    """Lazy-import a task so `so101 doctor` works even if a task's deps are broken."""
    def run(a, prof):
        import importlib
        try:
            mod = importlib.import_module(f".{module}", __package__)
        except ModuleNotFoundError as e:
            if e.name and e.name.endswith(module.split(".")[-1]):
                print(f"'{module}' is not available yet (framework subset 2).", file=sys.stderr)
                return 2
            raise
        sess = Session(prof.run_dir, a.agent)
        try:
            return getattr(mod, func)(a, prof, sess) or 0
        except Stopped as e:
            sess.event("stopped", reason=str(e)); print(f"STOPPED: {e}", flush=True)
            return 3
        finally:
            sess.close()
    return run


def build_parser():
    p = argparse.ArgumentParser(prog="so101", description=__doc__.split("\n\n")[0])
    p.add_argument("--profile", help="profile name (default: $SO101_PROFILE or my_so101)")
    p.add_argument("--agent", action="store_true", default=None,
                   help="agent mode: answers via runs/<profile>/cmd.txt instead of stdin")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check setup; --measure records robot quirks (moves the arm)")
    d.add_argument("--measure", action="store_true")
    d.set_defaults(fn=_cmd_doctor)

    pr = sub.add_parser("profile", help="show / list profiles")
    pr.add_argument("action", nargs="?", default="show", choices=["show", "list"])
    pr.set_defaults(fn=_cmd_profile)

    c = sub.add_parser("calibrate", help="camera intrinsics | table mapping | colour thresholds")
    c.add_argument("what", choices=["camera", "table", "hsv"])
    c.add_argument("--color", default="orange")
    c.add_argument("--grid", default="all", help="table: main | offset | all")
    c.set_defaults(fn=lambda a, prof: _task(f"calibration.{a.what}")(a, prof))

    det = sub.add_parser("detect", help="live detection preview")
    det.add_argument("colors", nargs="*", default=["orange"])
    det.set_defaults(fn=_task("vision.preview"))

    for name, mod, helptext in [("pick-place", "tasks.pick_place", "pick a cube, place at a fixed spot (trials)"),
                                ("basket", "tasks.basket", "pick a cube anywhere, drop it in the basket (trials)"),
                                ("multi", "tasks.multi", "clear several objects into the basket"),
                                ("stack", "tasks.stack", "drop the hollow brick over the cube"),
                                ("draw", "tasks.draw", "draw shapes with a pen held across the jaws")]:
        t = sub.add_parser(name, help=helptext)
        t.add_argument("--trials", type=int, default=1)
        t.add_argument("--objects", nargs="*", help="multi: object names from the profile (default: all)")
        t.add_argument("--hover", action="store_true", help="stack/pick-place: hover check only, no grasp")
        t.set_defaults(fn=_task(mod))

    dm = sub.add_parser("demo", help="practice the prompt/answer protocol (no hardware, nothing moves)")
    dm.set_defaults(fn=_task("tasks.demo"))

    ag = sub.add_parser("agent", help="drive a task from an AI: start | wait | status | answer TEXT | stop")
    ag.add_argument("op_args", nargs=argparse.REMAINDER)
    ag.set_defaults(fn=lambda a, prof: __import__("so101.agent", fromlist=["main"]).main(a.op_args, prof.name))

    r = sub.add_parser("report", help="metrics from logged trials (markdown + csv)")
    r.set_defaults(fn=_task("report"))
    return p


def main(argv=None):
    stdout_utf8()
    a = build_parser().parse_args(argv)
    prof = P.load(a.profile)
    return a.fn(a, prof)


if __name__ == "__main__":
    sys.exit(main())
