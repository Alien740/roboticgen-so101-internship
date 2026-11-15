"""so101-robot - the only command the AI tab lets Claude Code run (when it uses your Claude login).

    so101-robot state
    so101-robot move REACH_CM HEIGHT_CM BASE_DEG [down|angled|forward]
    so101-robot gripper PERCENT            0 = closed ... 95 = open
    so101-robot pose home|clear_view|ready
    so101-robot joint NAME DEGREES         shoulder_pan shoulder_lift elbow_flex wrist_flex wrist_roll
    so101-robot motion NAME                hello yes no dance bow chomp
    so101-robot find                       what the overhead camera sees on the table
    so101-robot pick red|yellow            find it and pick it up
    so101-robot put REACH_CM BASE_DEG      put what it holds down there
    so101-robot draw start|answer WORDS|make WHAT [SIZE]|wait    pen drawing (see the AI tab)
    so101-robot wait SECONDS

Each call asks the running dashboard (POST /api/ai_tool) to do one move through its controller -
speed preset, limits, table floor, gripper guard and E-STOP apply - and waits until the arm stops.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SO101_DASHBOARD", "http://127.0.0.1:8000")


def parse(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, a = argv[0], argv[1:]
    num = lambda i: float(a[i])
    if cmd == "state" and not a:
        return "get_state", {}
    if cmd == "move" and len(a) in (3, 4):
        return "move_gripper_to", {"reach_cm": num(0), "height_cm": num(1), "base_deg": num(2),
                                   "pointing": a[3] if len(a) == 4 else "down"}
    if cmd == "gripper" and len(a) == 1:
        return "set_gripper", {"percent": num(0)}
    if cmd == "pose" and len(a) == 1:
        return "go_to_pose", {"name": a[0]}
    if cmd == "joint" and len(a) == 2:
        return "set_joint", {"joint": a[0], "degrees": num(1)}
    if cmd == "motion" and len(a) == 1:
        return "play_motion", {"name": a[0]}
    if cmd == "find" and not a:
        return "find_objects", {}
    if cmd == "pick" and len(a) == 1 and a[0] in ("red", "yellow"):
        return "pick_up", {"color": a[0]}
    if cmd == "draw" and a:
        sub, rest = a[0], a[1:]
        if sub == "start" and not rest:
            return "draw_start", {}
        if sub == "answer" and rest:
            return "draw_answer", {"text": " ".join(rest)}
        if sub == "make" and 1 <= len(rest) <= 2:
            return "draw_make", {"what": rest[0], "size_mm": float(rest[1]) if len(rest) == 2 else 0.0}
        if sub == "wait" and not rest:
            return "draw_wait", {}
    if cmd == "put" and len(a) == 2:
        return "put_down", {"reach_cm": num(0), "base_deg": num(1)}
    if cmd == "wait" and len(a) == 1:
        return "wait", {"seconds": num(0)}
    raise SystemExit(f"bad command: {' '.join(argv)}\n{__doc__}")


def main(argv=None):
    name, args = parse(sys.argv[1:] if argv is None else argv)
    req = urllib.request.Request(f"{URL}/api/ai_tool", json.dumps({"name": name, "input": args}).encode(),
                                 {"X-SO101": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"error: {json.load(e).get('error', e.reason)}")
        return 1
    except urllib.error.URLError as e:
        print(f"error: dashboard not reachable at {URL} ({e.reason})")
        return 1
    print(out["result"])
    return 1 if out.get("is_error") else 0


if __name__ == "__main__":
    sys.exit(main())
